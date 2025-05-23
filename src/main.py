import argparse
import asyncio
import glob
import logging
import os
import shutil
import tarfile
import traceback
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List

import aiohttp
from tqdm.asyncio import tqdm

from models import Error, Firmware, Ok, Response, Result
from scrape_key import decrypt_dmg
from utils.download import download_file
from utils.fs import (bundles_glob, delete_non_bundles,
                      is_firmware_version_done, is_firmware_version_ignored,
                      put_metadata, system_has_parent)
from utils.git import process_files_with_git
from utils.hash import calculate_hash
from utils.shell import run_command

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PRODUCT_CODES: Dict[str, List[str]] = {
    "iPad": [
        "16,6",
        "16,4",
        "16,2",
        "15,8",
        "15,6",
        "15,5",
        "15,4",
        "15,3",
        "14,11",
        "14,9",
        "14,6",
        "14,4",
        "14,2",
        "13,19",
        "13,17",
        "13,11",
        "13,10",
        "13,7",
        "13,5",
        "13,2",
        "12,2",
        "11,7",
        "11,4",
        "11,2",
        "8,12",
        "8,10",
        "8,8",
        "8,7",
        "8,4",
        "8,3",
        "7,12",
        "7,6",
        "7,4",
        "7,2",
        "6,12",
        "6,8",
        "6,4",
        "5,4",
        "5,2",
        "4,9",
        "4,8",
        "4,6",
        "4,5",
        "4,3",
        "4,2",
        "3,6",
        "3,5",
        "3,3",
        "3,2",
        "2,7",
        "2,6",
        "2,3",
        "2,2",
        "1,1",
    ],
    "iPhone": [
        "14,6",
        "14,5",
        "14,4",
        "14,3",
        "14,2",
        "13,4",
        "13,3",
        "13,2",
        "13,1",
        "12,8",
        "12,5",
        "12,3",
        "12,1",
        "11,8",
        "11,6",
        "11,4",
        "11,2",
        "10,6",
        "10,5",
        "10,4",
        "10,4",
        "10,2",
        "10,1",
        "9,4",
        "9,3",
        "9,2",
        "9,1",
        "8,4",
        "8,2",
        "8,1",
        "7,2",
        "7,1",
        "6,2",
        "6,1",
        "5,4",
        "5,3",
        "5,2",
        "5,1",
        "4,1",
        "3,3",
        "3,2",
        "3,1",
        "2,1",
    ],
}


async def decrypt_dmg_aea(
    ipsw_file: Path, dmg_file: Path, output: Path
) -> Result[None, str]:
    logger.info(f"decrypting {dmg_file}")

    if shutil.which("ipsw") is None:
        logger.warning("ipsw is not installed")
        deb_path = Path("ipsw.deb")

        if not deb_path.exists():
            await run_command(
                    f"wget https://github.com/blacktop/ipsw/releases/download/v3.1.544/ipsw_3.1.544_linux_x86_64.deb --output-document {deb_path}"
            )

        await run_command(f"sudo dpkg -i {deb_path}")
        deb_path.unlink(missing_ok=True)

    try:
        await run_command(
            f"ipsw extract --fcs-key {ipsw_file} --output {output}"
        )
    except Exception as e:
        return Error(f"Extraction failed: {e}")

    pem_files = [Path(p) for p in glob.glob(f"{output}/**/*.pem")]

    matching_pem = next((pem for pem in pem_files if pem.stem == dmg_file.name), None)

    if matching_pem:
        logger.info(f"Found a matching PEM file: {matching_pem}")
        pem_file = matching_pem
    else:
        if pem_files:
            logger.warning("No matched PEM, using the first one")
            pem_file = pem_files[0]
        else:
            return Error("No PEM file found.")

    try:
        logger.info("Decrypting")
        await run_command(
                f"ipsw fw aea --pem {pem_file} {dmg_file} --output {output}"
        )
    except Exception as e:
        return Error(f"Decryption failed: {e}")

    # we don't need the .dmg.aea
    dmg_file.unlink(missing_ok=True)

    # we don't also need the .pem folder that was created by `ipsw`
    shutil.rmtree(pem_file.parent)

    return Ok(None)


async def extract_the_biggest_dmg(
    dmg_file: Path,
    output: Path,
    firmware: Firmware,
    ignored_firmwares_file: Path,
    *,
    skip_extraction: bool = False,
) -> Result[bool, str]:
    """
    it would return a bool whether it the `System` has a parent or not
    """

    def cleanup():
        logger.info(f"Cleaning up extracted file: {biggest_dmg_file_path}")
        biggest_dmg_file_path.unlink(missing_ok=True)

        logger.info(f"Cleaning up original IPSW file: {dmg_file}")
        dmg_file.unlink(missing_ok=True)

    async def ignore():
        shutil.rmtree(output)

        await put_metadata(
            ignored_firmwares_file,
            "ignored",
            lambda ign: (ign or []) + [firmware.version],
        )

    logger.info(f"Extracting the biggest DMG from {dmg_file}")

    try:
        with zipfile.ZipFile(dmg_file) as zip_file:
            biggest_dmg = max(zip_file.infolist(), key=lambda x: x.file_size)

            # not sure if there's one, idk, but if the biggest file is neithr a .dmg or .dmg.aea then ignore it
            if not biggest_dmg.filename.endswith((".dmg", ".dmg.aea")):
                error_msg = "There was no .dmg in the .ipsw file, ignoring"
                logger.warning(error_msg)

                await ignore()
                return Error(error_msg)

            biggest_dmg_file_path = output / biggest_dmg.filename

            logger.debug(
                f"Biggest DMG found: {biggest_dmg.filename} ({biggest_dmg.file_size} bytes)"
            )

            if (
                not biggest_dmg_file_path.exists()
                or biggest_dmg_file_path.stat().st_size != biggest_dmg.file_size
            ) and not skip_extraction:
                logger.info(f"Extracting {biggest_dmg.filename} to {output}")
                with (
                    zip_file.open(biggest_dmg) as source,
                    open(biggest_dmg_file_path, "wb") as target,
                    tqdm(
                        total=biggest_dmg.file_size,
                        unit="B",
                        unit_scale=True,
                        desc=f"Extracting {biggest_dmg.filename}",
                    ) as progress,
                ):
                    while True:
                        chunk = source.read(8192)
                        if not chunk:
                            break

                        target.write(chunk)
                        progress.update(len(chunk))

            else:
                logger.info("Skipping dmg extraction (file already exists)")

        if "aea" in biggest_dmg_file_path.suffix:
            logger.info("Detected 'aea' in file suffix, starting decryption process")
            decryption_result = await decrypt_dmg_aea(
                dmg_file, biggest_dmg_file_path, output
            )

            if isinstance(decryption_result, Error):
                logger.error("Decryption process failed")

                return decryption_result

            # removes the last .aea from a path, that's because while decrypting,
            # it already make a .dmg file with the same name
            biggest_dmg_file_path = (
                biggest_dmg_file_path.parent / biggest_dmg_file_path.stem
            )

        logger.info(f"Extracting bundles from {biggest_dmg_file_path} using 7z")

        has_parent = await system_has_parent(biggest_dmg_file_path)

        if isinstance(has_parent, Error):
            return has_parent

        command = [
            "7z",
            "x",
            biggest_dmg_file_path,
            f"-o{output}",
            "-aos",  # overwrite
            "-bd",  # no progress
            "-y",
            # if true, that means there is a parent folder for the `System` folder, so glob that
            f"{'*/' if has_parent.value else ''}System/Library/Carrier Bundles/*",  # where all the bundles are
        ]

        decryption_result = await run_command(
            command,
            check=False
        )

        stdout, stderr, returncode = decryption_result

        logger.debug(f"7z stdout: {stdout}")
        logger.debug(f"7z stderr: {stderr}")

        if returncode != 0:
            error_msg = f"Couldn't extract {dmg_file}, error: {stderr}"
            logger.error(error_msg)

            # usually with old firmwares, we must then decrypt it using a special key
            if "Cannot open the file as [Dmg] archive" in stderr:
                decrypt_result = await decrypt_dmg(
                    dmg_file,
                    biggest_dmg_file_path,
                    firmware.buildid,
                    firmware.identifier,
                )

                if isinstance(decrypt_result, Error):
                    return Error(f"Unable to extract the dmg, error: {decrypt_result}")

                # TODO: maybe we can just extract the new thing and move on and not re-run the entire function
                return await extract_the_biggest_dmg(
                    dmg_file,
                    output,
                    firmware,
                    ignored_firmwares_file,
                    skip_extraction=True,
                )

            return Error(error_msg)

        return has_parent

    finally:
        cleanup()


async def tar_and_hash_bundles(
    bundles: List[Path],
) -> Result[List[Dict[str, str | int]], str]:
    output_bundles: List[Dict[str, str | int]] = []

    for bundle in bundles:
        bundle_tar = bundle.with_suffix(".tar")

        with tarfile.open(bundle_tar, "w", format=tarfile.PAX_FORMAT) as tar:
            tar.add(bundle, arcname=bundle.name, recursive=True)

        sha1 = await calculate_hash(bundle_tar, "sha1")
        output_bundles.append(
            {
                "bundle_name": bundle_tar.stem,
                "sha1": sha1,
                "file_size": bundle_tar.stat().st_size,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    return Ok(output_bundles)


async def bake_ipcc(
    firmware: Firmware,
    session: aiohttp.ClientSession,
) -> bool:
    """
    it will return  whether the ipcc's are created or not
    """

    base_path = Path(firmware.identifier)
    base_path.mkdir(exist_ok=True)

    version_path = base_path / firmware.version
    version_path.mkdir(exist_ok=True)

    base_metadata_path = base_path / "metadata.json"
    base_metadata_path.touch(exist_ok=True)

    ignored_firmwares_metadata_path = (
            base_path / "ignored_firmwares.json"
            )
    ignored_firmwares_metadata_path.touch(exist_ok=True)

    bundles_metadata_path = version_path / "bundles.json"
    bundles_metadata_path.touch(exist_ok=True)

    try:
        start_time = datetime.now(UTC)

        if await is_firmware_version_ignored(ignored_firmwares_metadata_path, firmware.version):
            shutil.rmtree(version_path, ignore_errors=True)
            return False

        if await is_firmware_version_done(base_metadata_path, firmware.version):
            return False

        ipsw_file = await download_file(firmware, version_path, session, ignored_firmwares_metadata_path)

        if isinstance(ipsw_file, Error):
            raise RuntimeError(ipsw_file)

        extract_big_result = await extract_the_biggest_dmg(
            ipsw_file.value,
            version_path,
            firmware,
            ignored_firmwares_metadata_path,
        )

        if isinstance(extract_big_result, Error):
            raise RuntimeError(extract_big_result)

        has_parent = extract_big_result.value

        bundles_folders = list(
            bundles_glob(version_path, has_parent)
        )

        new_bundles_folders = delete_non_bundles(
            version_path, bundles_folders, has_parent
        )

        if isinstance(new_bundles_folders, Error):
            raise RuntimeError(new_bundles_folders)

        tarred_with_hash_bundles = await tar_and_hash_bundles(
            new_bundles_folders.value
        )

        # we don't need the .bundle folder after tarring it (compress it to a .tar)
        for path in new_bundles_folders.value:
            shutil.rmtree(path)

        if isinstance(tarred_with_hash_bundles, Error):
            raise RuntimeError(tarred_with_hash_bundles)

        tarred_bundles_value = tarred_with_hash_bundles.value

        await put_metadata(
            bundles_metadata_path,
            "bundles",
            lambda acc: (acc or []) + tarred_bundles_value,
        )

        elapsed = (datetime.now(UTC) - start_time).total_seconds()

        await put_metadata(
            base_metadata_path,
            "fw",
            lambda acc: (acc or [])
            + [
                {
                    "version": firmware.version,
                    "buildid": firmware.buildid,
                    "downloaded_at": datetime.now(UTC).isoformat(),
                    "processing_time_sec": elapsed,
                }
            ],
        )

        return True

    except Exception as e:
        shutil.rmtree(version_path, ignore_errors=True)

        logger.error(
            f"Something went wrong, {e}\n traceback: {traceback.format_exc()}"
        )

    return False


async def fetch_and_bake(
    session: aiohttp.ClientSession,
    code: str,
    product: str,
    devices_semaphore: asyncio.Semaphore,
    git_mode: bool,
):
    async with devices_semaphore:

        model = f"{product}{code}"
        response = await session.get(
            f"https://api.ipsw.me/v4/device/{model}", params={"type": "ipsw"}
        )

        if response.status != 200:
            logger.error(f"Failed to fetch data for {model}: {await response.text()}")
            return

        parsed_data = Response.from_dict(await response.json())
        if not parsed_data.firmwares:
            logger.warning(f"No firmwares found for {model}")
            return

        ident = parsed_data.firmwares[0].identifier

        processed_count = 0
        for firmware in parsed_data.firmwares:
            # if git_mode:
            #     await copy_previous_metadata(ident)

            if await bake_ipcc(firmware, session):

                processed_count += 1
                if git_mode:
                    await process_files_with_git(ident, firmware.version)

        if processed_count == 0:
            shutil.rmtree(ident)

async def main():
    app = argparse.ArgumentParser("OpeniTools-IPCC")

    app.add_argument(
        "--git",
        "-g",
        help="Use it if you want to upload the files to Github (setup your git before)",
        required=False,
        default=False,
        action="store_true",
    )

    app.add_argument(
        "-j",
        "--concurrent-jobs",
        help="The number of concurrent firmware extraction jobs (default: 3)",
        required=False,
        default=3,
        type=int
    )

    args = app.parse_args()

    git_mode: bool = args.git

    # go back before 'src'
    os.chdir(Path(__file__).resolve().parents[1])


    if git_mode:
        await run_command("git switch files")

    devices_semaphore = asyncio.Semaphore(args.concurrent_jobs)

    async with aiohttp.ClientSession() as session:
        async with asyncio.TaskGroup() as main_group:
            for product, codes in PRODUCT_CODES.items():
                for code in codes:
                    main_group.create_task(fetch_and_bake(session, code, product, devices_semaphore, git_mode))


if __name__ == "__main__":
    asyncio.run(main())

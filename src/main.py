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
from typing import Dict, List, Optional

import aiofiles
import aiohttp
from aiohttp_socks import ProxyConnector
from tqdm.asyncio import tqdm

from config import cfg
from models import BundleMetadata, Error, Firmware, Ok, Response, Result
from products import init_models, models
from scrape_key import decrypt_dmg
from utils.download import download_file
from utils.fs import (
    bundles_glob,
    delete_non_bundles,
    is_firmware_version_done,
    is_firmware_version_ignored,
    put_metadata,
    system_has_parent,
)
from utils.git import ignore_firmware, process_files_with_git
from utils.hash import calculate_hash
from utils.helpers import install_ipsw
from utils.shell import run_command

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def decrypt_dmg_aea(
    ipsw_file: Path, dmg_file: Path, output: Path
) -> Result[None, str]:
    """Decrypt DMG.AEA file using ipsw tool."""
    logger.info(f"Decrypting {dmg_file}")

    try:
        # Check if ipsw is installed
        if shutil.which("ipsw") is None:
            logger.warning("ipsw is not installed")
            await install_ipsw()

        # Extract with ipsw
        stdout, stderr, return_code = await run_command(
            f"ipsw extract --fcs-key {ipsw_file} --output {output}"
        )
        if return_code != 0:
            return Error(f"Extraction failed: {stdout} | {stderr}")

        # Find PEM files
        pem_files = [Path(p) for p in glob.glob(f"{output}/**/*.pem", recursive=True)]
        if not pem_files:
            return Error("No PEM file found.")

        # Find matching PEM or use first one
        pem_file = (
            next((p for p in pem_files if p.stem == dmg_file.name), None)
            or pem_files[0]
        )
        logger.info(f"Using PEM file: {pem_file}")

        # Decrypt
        stdout, stderr, return_code = await run_command(
            f"ipsw fw aea --pem {pem_file} {dmg_file} --output {output}"
        )
        if return_code != 0:
            return Error(f"Decryption failed: {stdout} | {stderr}")

        # Cleanup
        try:
            dmg_file.unlink(missing_ok=True)
            shutil.rmtree(pem_file.parent, ignore_errors=True)
        except Exception as cleanup_error:
            logger.warning(f"Cleanup failed: {cleanup_error}")

        return Ok(None)

    except Exception as e:
        return Error(f"Unexpected error in decrypt_dmg_aea: {str(e)}")


async def handle_aea_dmg(
    dmg_file: Path, biggest_dmg_file_path: Path, output: Path
) -> Result[Path, str]:
    decryption_result = await decrypt_dmg_aea(dmg_file, biggest_dmg_file_path, output)
    if isinstance(decryption_result, Error):
        return decryption_result

    return Ok(biggest_dmg_file_path.parent / biggest_dmg_file_path.stem)


async def get_biggest_dmg_file_in_zip(
    zip_file: zipfile.ZipFile,
) -> Result[zipfile.ZipInfo, str]:
    biggest_dmg = max(
        [
            file
            for file in zip_file.infolist()
            if file.filename.endswith((".dmg", ".dmg.aea"))
        ],
        key=lambda file: file.file_size,
    )

    if not biggest_dmg:
        return Error("No .dmg or .dmg.aea files found in the IPSW")

    return Ok(biggest_dmg)


async def extract_the_biggest_dmg(
    dmg_file: Path,
    output: Path,
    firmware: Firmware,
    ignored_firmwares_file: Path,
    *,
    skip_extraction: bool = False,
) -> Result[bool, str]:
    """
    Extract the biggest DMG from IPSW file.

    It would also return whether the 'System' has a parent or not
    """
    logger.info(f"Extracting the biggest DMG from {dmg_file}")

    biggest_dmg_file_path: Optional[Path] = None

    try:
        # Verify ZIP file first
        if not zipfile.is_zipfile(dmg_file):
            return Error(f"File {dmg_file} is not a valid ZIP file")

        with zipfile.ZipFile(dmg_file) as zip_file:
            # Find biggest DMG file

            try:
                biggest_dmg = await get_biggest_dmg_file_in_zip(zip_file)

                if isinstance(biggest_dmg, Error):
                    logger.warning(biggest_dmg.error)

                    await ignore_firmware(ignored_firmwares_file, firmware)
                    return biggest_dmg

                biggest_dmg = biggest_dmg.value
                biggest_dmg_file_path = output / biggest_dmg.filename

                logger.debug(
                    f"Biggest DMG found: {biggest_dmg.filename} ({biggest_dmg.file_size} bytes)"
                )

                # Extract if needed
                if (
                    not biggest_dmg_file_path.exists()
                    or biggest_dmg_file_path.stat().st_size != biggest_dmg.file_size
                ) and not skip_extraction:
                    logger.info(f"Extracting {biggest_dmg.filename} to {output}")

                    progress = tqdm(
                        total=biggest_dmg.file_size,
                        unit="B",
                        unit_scale=True,
                        desc=f"Extracting {biggest_dmg.filename}",
                    )

                    source = zip_file.open(biggest_dmg)

                    async with aiofiles.open(biggest_dmg_file_path, "wb") as target:
                        while True:
                            chunk = await asyncio.to_thread(
                                source.read, 16 * 1024 * 1024
                            )
                            if not chunk:
                                break

                            await target.write(chunk)
                            progress.update(len(chunk))

                    progress.close()
                    source.close()

                else:
                    logger.info("Skipping DMG extraction (file already exists)")

            except Exception as zip_error:
                return Error(f"ZIP extraction error: {str(zip_error)}")

        # Handle AEA decryption if needed
        if biggest_dmg_file_path and ".aea" in biggest_dmg_file_path.suffixes:
            logger.info("Detected 'aea' in file suffix, starting decryption")
            handle_result = await handle_aea_dmg(
                dmg_file, biggest_dmg_file_path, output
            )

            if isinstance(handle_result, Error):
                return handle_result

            biggest_dmg_file_path = handle_result.value

        # Extract bundles
        if not biggest_dmg_file_path or not biggest_dmg_file_path.exists():
            return Error("DMG file not found after extraction")

        has_parent_result = await system_has_parent(biggest_dmg_file_path)
        if isinstance(has_parent_result, Error):
            return has_parent_result

        command = [
            "7z",
            "x",
            str(biggest_dmg_file_path),
            f"-o{output}",
            "-aos",  # overwrite
            "-bd",  # no progress
            "-y",
            f"{'*/' if has_parent_result.value else ''}System/Library/Carrier Bundles/*",
        ]

        extract_result = await run_command(command, check=False)
        if isinstance(extract_result, Error):
            return extract_result

        stdout, stderr, returncode = extract_result
        logger.debug(f"7z stdout: {stdout}")
        logger.debug(f"7z stderr: {stderr}")

        if returncode != 0:
            if "Cannot open the file as [Dmg] archive" in stderr:
                decrypt_result = await decrypt_dmg(
                    dmg_file,
                    biggest_dmg_file_path,
                    firmware.buildid,
                    firmware.identifier,
                )
                if isinstance(decrypt_result, Error):
                    if decrypt_result.error.startswith("decryption failed"):
                        logger.warning("was not able to decrypt the dmg, ignoring")
                        # FIXME: something is wrong with pushing the ignored firmware while other files exists
                        await ignore_firmware(ignored_firmwares_file, firmware)

                    return Error(f"Unable to extract the DMG: {decrypt_result}")

                return await extract_the_biggest_dmg(
                    dmg_file,
                    output,
                    firmware,
                    ignored_firmwares_file,
                    skip_extraction=True,
                )
            return Error(f"7z extraction failed: {stderr}")

        return has_parent_result

    except Exception as e:
        return Error(f"Unexpected error in extract_the_biggest_dmg: {str(e)}")
    finally:
        # Cleanup
        if biggest_dmg_file_path and biggest_dmg_file_path.exists():
            try:
                biggest_dmg_file_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to cleanup DMG file: {str(e)}")

        if dmg_file.exists():
            try:
                dmg_file.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to cleanup IPSW file: {str(e)}")


async def tar_and_hash_bundles(
    bundles: List[Path],
) -> Result[List[BundleMetadata], str]:
    """Create tar files from bundles and calculate their hashes."""
    output_bundles: List[BundleMetadata] = []

    for bundle in bundles:
        try:
            bundle_tar = bundle.with_suffix(".tar")
            logger.info(f"Creating tar for {bundle.name}")

            # Create tar file
            with tarfile.open(bundle_tar, "w", format=tarfile.PAX_FORMAT) as tar:
                tar.add(bundle, arcname=bundle.name, recursive=True)

            # Calculate hash
            sha1_result = await calculate_hash(bundle_tar, "sha1")

            # Add metadata
            output_bundles.append(
                {
                    "bundle_name": bundle_tar.stem,
                    "sha1": sha1_result,
                    "file_size": bundle_tar.stat().st_size,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )

            # Cleanup bundle folder
            try:
                shutil.rmtree(bundle)
            except Exception as e:
                logger.warning(f"Failed to cleanup bundle {bundle}: {str(e)}")

        except Exception as e:
            return Error(f"Failed to process bundle {bundle.name}: {str(e)}")

    return Ok(output_bundles)


async def bake_ipcc(
    firmware: Firmware,
    session: aiohttp.ClientSession,
) -> bool:
    """Process firmware to extract IPCC files."""
    base_path = Path(firmware.identifier)
    version_path = base_path / firmware.version

    try:
        # Create directories if they don't exist
        base_path.mkdir(exist_ok=True, parents=True)
        version_path.mkdir(exist_ok=True)

        # Initialize metadata files
        base_metadata_path = base_path / "metadata.json"
        ignored_firmwares_metadata_path = base_path / "ignored_firmwares.json"
        bundles_metadata_path = version_path / "bundles.json"

        for p in [
            base_metadata_path,
            ignored_firmwares_metadata_path,
            bundles_metadata_path,
        ]:
            p.touch(exist_ok=True)

        start_time = datetime.now(UTC)

        # Check if version should be ignored
        if await is_firmware_version_ignored(
            ignored_firmwares_metadata_path, firmware.version
        ):
            if not cfg.retry_ignored:
                shutil.rmtree(version_path, ignore_errors=True)
                return False

            # remove it from the ignored in an attempt to retry processing it again
            await put_metadata(
                ignored_firmwares_metadata_path,
                "ignored",
                lambda firmwares: [
                    f for f in (firmwares or []) if f != firmware.version
                ],
            )

            firmware.was_ignored = True

        # Check if version already processed
        if await is_firmware_version_done(base_metadata_path, firmware.version):
            if not cfg.reprocess:
                return False

            await put_metadata(
                base_metadata_path,
                "fw",
                lambda firmwares: [
                    f for f in (firmwares or []) if f.version != firmware.version
                ],
            )

            # delete the version dir and make it again
            shutil.rmtree(version_path, ignore_errors=True)
            version_path.mkdir(exist_ok=True)

        # Download IPSW file
        ipsw_result = await download_file(
            firmware,
            version_path,
            session,
            ignored_firmwares_metadata_path,
        )
        if isinstance(ipsw_result, Error):
            raise RuntimeError(ipsw_result)

        # Extract DMG
        extract_result = await extract_the_biggest_dmg(
            ipsw_result.value,
            version_path,
            firmware,
            ignored_firmwares_metadata_path,
        )
        if isinstance(extract_result, Error):
            raise RuntimeError(extract_result)

        # Process bundles
        bundles_folders = list(bundles_glob(version_path, extract_result.value))
        new_bundles_result = delete_non_bundles(
            version_path, bundles_folders, extract_result.value
        )
        if isinstance(new_bundles_result, Error):
            raise RuntimeError(new_bundles_result)

        # Create tar files and hashes
        tar_result = await tar_and_hash_bundles(new_bundles_result.value)
        if isinstance(tar_result, Error):
            raise RuntimeError(tar_result)

        # Update metadata
        await put_metadata(
            bundles_metadata_path,
            "bundles",
            lambda acc: (acc or []) + tar_result.value,
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
        logger.error(
            f"Error processing {firmware.identifier} {firmware.version}: {str(e)}\n{traceback.format_exc()}"
        )
        shutil.rmtree(version_path, ignore_errors=True)
        return False


async def fetch_and_bake(
    model: str,
    devices_semaphore: asyncio.Semaphore,
) -> None:
    """Fetch and process firmware for a specific device."""
    async with devices_semaphore:
        try:
            logger.info(f"Processing device {model}")

            connector = (
                ProxyConnector.from_url(cfg.http_proxy)
                if cfg.http_proxy and cfg.proxy_target.is_ipsw_or_all()
                else None
            )
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    f"https://api.ipsw.me/v4/device/{model}", params={"type": "ipsw"}
                ) as response:
                    if response.status != 200:
                        logger.error(
                            f"Failed to fetch data for {model}: {await response.text()}"
                        )
                        return

                    parsed_data = Response.from_dict(await response.json())

                    if not parsed_data.firmwares:
                        logger.warning(f"No firmwares found for {model}")
                        return

                    processed_count = 0
                    # assuming there's at least one firmware
                    current_ident = parsed_data.firmwares[0].identifier

                    for firmware in parsed_data.firmwares:
                        if await bake_ipcc(firmware, session):
                            processed_count += 1
                            if cfg.upload_github:
                                git_result = await process_files_with_git(firmware)
                                if isinstance(git_result, Error):
                                    logger.error(
                                        f"Git processing failed: {git_result.value}"
                                    )

                    if processed_count == 0:
                        shutil.rmtree(current_ident, ignore_errors=True)

        except Exception as e:
            logger.error(
                f"Error processing device {model}: {str(e)}\n{traceback.format_exc()}"
            )


async def main() -> None:
    # Change to parent directory
    os.chdir(Path(__file__).resolve().parents[1])

    init_models()

    if cfg.upload_github:
        stdout, stderr, return_code = await run_command("git switch files")
        if return_code != 0:
            logger.error(f"Failed to switch git branch: {stdout} | {stderr}")
            return

    devices_semaphore = asyncio.Semaphore(cfg.jobs)

    try:
        async with asyncio.TaskGroup() as tg:
            for model in models:
                tg.create_task(
                    fetch_and_bake(
                        model,
                        devices_semaphore,
                    )
                )
    except Exception as e:
        logger.error(f"Error in main task group: {str(e)}")


if __name__ == "__main__":
    asyncio.run(main())

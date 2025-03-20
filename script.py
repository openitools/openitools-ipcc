import json
import aiohttp
import asyncio
import hashlib
from typing import  Callable, Dict, List, Generic, Optional,  TypeVar, Union
from dataclasses import dataclass
from datetime import datetime
from pprint import pprint
from pathlib import Path
from tqdm.asyncio import tqdm
import zipfile
import subprocess
import glob
import shutil

IPHONES_PRODUCT_CODES: List[str] = [
    "7,1", "7,2", "8,1", "8,2", "8,4", "9,1", "9,2", "9,3", "9,4",
    "10,1", "10,2", "10,4", "10,4", "10,5", "10,6", "11,2", "11,4",
    "11,6", "11,8", "12,1", "12,3", "12,5", "12,8", "13,1", "13,2",
    "13,3", "13,4", "14,2", "14,3", "14,4", "14,5", "14,6",
]


T = TypeVar("T")
E = TypeVar("E")

# for json writing callbacks
J = TypeVar("J")

@dataclass
class Ok(Generic[T]):
    value: T

@dataclass
class Error(Generic[E]):
    error: E

Result = Union[Ok[T], Error[E]]

@dataclass
class Firmwares:
    identifier: str
    version: str
    buildid: str
    sha1sum: str
    md5sum: str
    filesize: int
    url: str
    releasedate: datetime
    uploaddate: datetime
    signed: bool

    @staticmethod
    def from_dict(data: dict) -> "Firmwares":
        return Firmwares(
            identifier=data["identifier"],
            version=data["version"],
            buildid=data["buildid"],
            sha1sum=data["sha1sum"],
            md5sum=data["md5sum"],
            filesize=data["filesize"],
            url=data["url"],
            releasedate=datetime.fromisoformat(data["releasedate"]),
            uploaddate=datetime.fromisoformat(data["uploaddate"]),
            signed=data["signed"]
        )

@dataclass
class Response:
    name: str
    identifier: str
    firmwares: List[Firmwares]
    boardconfig: str
    platform: str
    cpid: int
    bdid: int

    @staticmethod
    def from_dict(data: dict) -> "Response":
        return Response(
            name=data["name"],
            identifier=data["identifier"],
            firmwares=[Firmwares.from_dict(fw) for fw in data["firmwares"]],
            boardconfig=data["boardconfig"],
            platform=data["platform"],
            cpid=data["cpid"],
            bdid=data["bdid"]
        )

async def calculate_hash(file_path: Path) -> str:
    hash_func = hashlib.sha1()
    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(4096), b""):  
            hash_func.update(chunk)

    return hash_func.hexdigest()


async def download_file(firmware: Firmwares, version_folder: Path,  session: aiohttp.ClientSession) -> Result[Path, str]:
    file_path = version_folder / f"{firmware.identifier}-{firmware.version}.ipsw"

    if file_path.exists():
        if (await calculate_hash(file_path)) == firmware.sha1sum:
            return Ok(file_path)

        print("Detected a corrupted file, redownloading")
        file_path.unlink()

    try:
        async with session.get(firmware.url, timeout=aiohttp.ClientTimeout(400)) as response:
            if response.status != 200:
                return Error(f"Failed to download {firmware.identifier}: {response.status} {response.reason}")

            total_size = int(response.headers.get("Content-Length", 0))

            with open(file_path, "wb") as file, tqdm(
                total=total_size, unit="B", unit_scale=True, desc=str(file_path)
            ) as progress:
                async for chunk in response.content.iter_chunked(8192):
                    file.write(chunk)
                    progress.update(len(chunk))

    except aiohttp.ClientError as e:
        file_path.unlink(missing_ok=True)  # remove partially downloaded file on error
        return Error(f"Network error: {e}")


    if (await calculate_hash(file_path)) != firmware.sha1sum:
        return Error(f"Hash mismatch for {file_path}")

    return Ok(file_path)

async def put_metadata(metadata_path: Path, key: str, callback: Callable[[Optional[J]], J]) -> Result[None, str]:
    """Read & update JSON metadata using a callback."""
    try:
        metadata = json.loads(metadata_path.read_text() or "{}")
        metadata[key] = callback(metadata.get(key))
        metadata_path.write_text(json.dumps(metadata, indent=4))
    except json.JSONDecodeError as e:
        return Error(f"Invalid JSON: {e}")
    return Ok(None)

async def extract_the_biggest_dmg(file: Path, output: Path) -> Result[None, str]:
    with zipfile.ZipFile(file) as zip_file:
        biggest_dmg = max(zip_file.infolist(), key=lambda x: x.file_size)
        biggest_dmg_file_path = Path(biggest_dmg.filename).resolve()
    
        if not biggest_dmg_file_path.exists() or biggest_dmg_file_path.stat().st_size == biggest_dmg.file_size:
            zip_file.extract(biggest_dmg)

        command = [
            '7z', 'x', biggest_dmg_file_path,
            f'-o{output}',
            f'-aos', # overwrite
            f'-bd', # no progress
            f'-y', 
            f'System/Library/Carrier Bundles/*' # where all the bundles are
        ]

        result = subprocess.run(command, stdout=subprocess.PIPE)

        if result.stderr:
            return Error(f"Couldn't extract {file}, error: {result.stderr}")

        biggest_dmg_file_path.unlink()
        file.unlink()

        return Ok(None)

async def bundles_glob(path: Path) -> List[str]:
    return glob.glob(f"{path}/System/Library/Carrier Bundles/**/*.bundle")

async def delete_non_bundles(base_path: Path, bundles: List[Path]) -> Result[List[Path], str]:
    """Move bundles and clean up other files."""
    for bundle in bundles:
        shutil.move(str(bundle), str(base_path))

    shutil.rmtree(base_path / "System", ignore_errors=True)
    return Ok([base_path / bundle.name for bundle in bundles])

async def zip_and_hash_bundles(bundles: List[Path]) -> Result[List[Dict[str, str]], str]:
    output_bundles: List[Dict[str, str]] = []

    for bundle in bundles:
        bundle_zip = str(bundle).removesuffix(".bundle") + ".zip"

        with zipfile.ZipFile(bundle_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in bundle.walk():
                for file in files:
                    file_path = root / file

                    zipf.write(file_path, file_path.relative_to(bundle))  
        output_bundles.append({bundle_zip: await calculate_hash(Path(bundle_zip))})


    return Ok(output_bundles)


async def bake_ipcc(response: "Response", session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> Result[None, str]:
    for firmware in response.firmwares:
        async with semaphore:
            base_path = Path(firmware.identifier)
            base_path.mkdir(exist_ok=True)

            version_path = base_path / firmware.version
            version_path.mkdir(exist_ok=True)

            json_metadata_path = base_path / "metadata.json"
            json_metadata_path.touch(exist_ok=True)

            if firmware.version in json_metadata_path.read_text():
                continue

            download_result = await download_file(firmware, version_path, session)

            if isinstance(download_result, Error):
                return download_result

            extract_big_result = await extract_the_biggest_dmg(download_result.value, version_path)

            if isinstance(extract_big_result, Error):
                return extract_big_result

            bundles_folders = list(map(lambda s: Path(s), await bundles_glob(version_path)))

            new_bundles_folders = await delete_non_bundles(version_path, bundles_folders)
 
            if isinstance(new_bundles_folders, Error):
                return new_bundles_folders

            zipped_with_hash_bundles = await zip_and_hash_bundles(new_bundles_folders.value)

            for path in new_bundles_folders.value:
                shutil.rmtree(path)

            if isinstance(zipped_with_hash_bundles, Error):
                return zipped_with_hash_bundles

            bundles_metadata_path = version_path / "bundles.json"
            bundles_metadata_path.touch(exist_ok=True)

            for file in zipped_with_hash_bundles.value:
                for (bundle, bundle_hash) in file.items():
                    bundle = Path(bundle).name.removesuffix(".zip")

                    await put_metadata(bundles_metadata_path, "bundles", lambda acc: {**(acc or {}), bundle: bundle_hash})

            await put_metadata(json_metadata_path, "fw", lambda acc: (acc or []) + [firmware.version])

    return Ok(None)



async def fetch_and_bake(session: aiohttp.ClientSession, iphone_code: str, semaphore: asyncio.Semaphore):
    model = f"iPhone{iphone_code}"
    response = await session.get(f"https://api.ipsw.me/v4/device/{model}", params={"type": "ipsw"})

    if response.status == 200:
        parsed_data = Response.from_dict(await response.json())
        bake_result = await bake_ipcc(parsed_data, session, semaphore)

        if isinstance(bake_result, Error):
            pprint(f"Error: {bake_result.error}")
    else:
        print(f"something went wrong while baking iPhone{iphone_code}")
        print(f"error: {await response.text()}")

async def main():
    semaphore = asyncio.Semaphore(5) 

    async with aiohttp.ClientSession() as session:
        async with asyncio.TaskGroup() as group:

            for iphone_code in IPHONES_PRODUCT_CODES:
                task = fetch_and_bake(session, iphone_code, semaphore)
                group.create_task(task)


if __name__ == "__main__":
    asyncio.run(main())

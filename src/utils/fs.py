import asyncio
import glob
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, List, Optional, TypeVar

import aiofiles
import aiohttp
from tqdm.asyncio import tqdm

from models import Error, Firmware, Ok, Result
from utils import logger
from utils.hash import compare_either_hash

# for json writing callbacks
J = TypeVar("J")

async def is_file_ready(file_path: Path, firmware: Firmware) -> bool:
    """
    returns True if the file exists and the hash matches, otherwise remove it and return False
    """
    
    if file_path.exists():
        if await compare_either_hash(file_path, firmware):
            logger.info("ipsw file already exists, using it")
            return True

        logger.info("Detected a corrupted file, redownloading")
        file_path.unlink()

    return False

async def write_with_progress(
    resp: aiohttp.ClientResponse,
    file_path: Path,
    total_bytes: int,
    chunk_size: int = 8_192
) -> None:
    """Helper to write response.content → disk with a tqdm bar."""
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Open sync—in practice, IPSW writes are large and async file libs
    # often perform worse than plain open().
    with open(file_path, "wb") as f, \
         tqdm(total=total_bytes, unit="B", unit_scale=True, desc=str(file_path)) as bar:
        async for chunk in resp.content.iter_chunked(chunk_size):
            f.write(chunk)
            bar.update(len(chunk))


async def cleanup_file(file_path: Path) -> None:
    """Delete the file if it exists, swallowing errors."""
    try:
        file_path.unlink()
    except FileNotFoundError:
        pass



async def put_metadata(
    metadata_path: Path, key: str, callback: Callable[[Optional[J]], J]
) -> Result[None, str]:
    """Read & update JSON metadata using a callback."""
    logger.info(f"updating {metadata_path}")
    try:
        try:
            async with aiofiles.open(metadata_path, "r") as f:
                    text = await f.read()

                    logger.debug(f"{metadata_path} content: '{text}'")
                    metadata = json.loads(text) if text.strip() else {}


        except FileNotFoundError:
            metadata = {}

        logger.debug(f"before: {metadata = }")

        metadata[key] = callback(metadata.get(key))
        metadata["updated_at"] = datetime.now(UTC).isoformat()


        logger.debug(f"after: {metadata = }")

        async with aiofiles.open(metadata_path, "w") as f:
            await f.write(json.dumps(metadata, indent=4))

        return Ok(None)

    except json.JSONDecodeError as e:
        return Error(f"Invalid JSON: {e}")
    except Exception as e:
        return Error(f"Unexpected error: {e}")


def bundles_glob(path: Path, has_parent: bool = False) -> List[Path]:
    return list(
        map(
            lambda s: Path(s).resolve(),
            glob.glob(
                f"{path}/{'*/' if has_parent else ''}System/Library/Carrier Bundles/**/*.bundle"
            ),
        )
    )


def delete_non_bundles(
    base_path: Path, bundles: List[Path], has_parent: bool = False
) -> Result[List[Path], str]:
    try:
        for bundle in bundles:
            shutil.move(bundle, base_path / bundle.name)

        if has_parent:
            system_dirs = list(base_path.glob("*/System"))
            if not system_dirs:
                return Error("No nested 'System' folder found")
            path = system_dirs[0].parent
        else:
            path = base_path / "System"

        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

        return Ok([base_path / bundle.name for bundle in bundles])

    except Exception as e:
        return Error(f"Failed to clean up: {e}")


async def system_has_parent(dmg_file: Path) -> Result[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        "7z",
        "l",
        dmg_file,
        "*/System",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if proc.stdout is None or proc.stderr is None:
        return Error("Failed to capture stdout/stderr from 7z")

    lines = set()

    start_collecting = False

    while True:
        # TODO: sometimes it freezes

        # stderr_data = await proc.stderr.read()
        #
        # if stderr_data:
        #     return Error(stderr_data.decode(errors="ignore"))

        line = await proc.stdout.readline()

        if line == b"":
            break

        if b"Date" in line and b"Time" in line:
            start_collecting = True

        if start_collecting:
            lines.add(line)

            if len(lines) > 10:
                break

    try:
        proc.kill()
    except ProcessLookupError:
        pass

    return Ok(len(lines) > 10)

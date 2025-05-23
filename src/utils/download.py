from pathlib import Path

import aiohttp

from models import Error, Firmware, Ok, Result
from utils import logger
from utils.fs import (cleanup_file, is_file_ready, put_metadata,
                      write_with_progress)
from utils.hash import compare_either_hash


async def download_file(
    firmware: Firmware,
    version_folder: Path,
    session: aiohttp.ClientSession,
    ignored_firmwares_file: Path
) -> Result[Path, str]:
    """
    Downloads the firmware and returns the path to the downloaded .ipsw file
    """
    file_path = version_folder / f"{firmware.identifier}-{firmware.version}.ipsw"

    # 1) If it’s already good, skip the download
    if await is_file_ready(file_path, firmware):
        return Ok(file_path)


    # 2) Do the HTTP GET
    try:
        resp = await session.get(
            firmware.url,
            timeout=aiohttp.ClientTimeout(total=1000)
        )
        resp.raise_for_status()
    except aiohttp.ClientResponseError as e:
        await cleanup_file(file_path)

        # Service Unavailable, probably on old ios
        if e.status == 503:
            await put_metadata(
                ignored_firmwares_file,
                "ignored",
                lambda ign: (ign or []) + [firmware.version],
            )

        return Error(f"Client Response Error: {e}")

    except aiohttp.ClientError as e:
        await cleanup_file(file_path)
        return Error(f"Client Error: {e}")

    except Exception as e:
        await cleanup_file(file_path)
        return Error(f"Unexpected error during request: {e}")

    # 3) Stream it to disk with a tqdm progress bar
    content_length = int(resp.headers.get("Content-Length", 0))
    try:
        await write_with_progress(resp, file_path, content_length)
    except Exception as e:
        await cleanup_file(file_path)
        return Error(f"Error writing file: {e}")

    # 4) Final hash check
    if not await compare_either_hash(file_path, firmware):
        logger.warning(f"Hash mismatch for {file_path}")

    return Ok(file_path)

import asyncio
import shutil
from pathlib import Path

import aiohttp

from models import Error, Firmware, Ok, Result
from utils import logger
from utils.fs import (cleanup_file, is_file_ready, put_metadata,
                      write_with_progress)
from utils.git import process_files_with_git
from utils.hash import compare_either_hash

MAX_RETRIES = 3
DELAY = 1
CHUNK_SIZE = 8_192

async def get_response(
    firmware: Firmware,
    session: aiohttp.ClientSession,
    ignored_firmwares_file: Path,
    git_mode: bool,
    file_path: Path,
) -> Result[aiohttp.ClientResponse, str]:
    file_size = 0 if not file_path.exists() else file_path.stat().st_size

    headers = {}
    if file_size > CHUNK_SIZE:
        headers["Range"] = f"bytes={file_size}"

    try:
        resp = await session.get(
                firmware.url, timeout=aiohttp.ClientTimeout(total=1000), headers=headers
        )
        resp.raise_for_status()
    except aiohttp.ClientResponseError as e:
        # Service Unavailable, probably on old ios
        if e.status == 503:
            await put_metadata(
                ignored_firmwares_file,
                "ignored",
                lambda ign: (ign or []) + [firmware.version],
            )

            shutil.rmtree(
                Path(firmware.identifier) / firmware.version, ignore_errors=True
            )

            if git_mode:
                await process_files_with_git(
                    firmware, "ignored {version} for {ident}"
                )

        return Error(f"Client Response Error: {e}")

    except aiohttp.ClientError as e:
        return Error(f"Client Error: {e}")

    except Exception as e:
        return Error(f"Unexpected error during request: {e}")

    return Ok(resp)

async def download_file(
    firmware: Firmware,
    version_folder: Path,
    session: aiohttp.ClientSession,
    ignored_firmwares_file: Path,
    git_mode: bool
) -> Result[Path, str]:
    """
    Downloads the firmware and returns the path to the downloaded .ipsw file
    """
    file_path = version_folder / f"{firmware.identifier}-{firmware.version}.ipsw"

    # If it’s already good, skip the download
    if await is_file_ready(file_path, firmware):
        return Ok(file_path)

    # retry resuming the download if error ocurred
    for attempt in range(1, MAX_RETRIES + 1):
        response = await get_response(firmware, session, ignored_firmwares_file, git_mode, file_path)

        if isinstance(response, Error):
            # we don't want to retry this one, because it's probably not going to be fixed by retrying
            await cleanup_file(file_path)
            return response

        response = response.value

        content_length = int(response.headers.get("Content-Length", 0))

        if content_length == 0:
            logger.warning(f"No Content-Length for {firmware.url}")

        try:
            await write_with_progress(response, file_path, content_length, CHUNK_SIZE)

            break
        except Exception as e:

            if attempt == MAX_RETRIES:
                await cleanup_file(file_path)

                return Error(f"Error writing file after {MAX_RETRIES} retries: {e}")

            await asyncio.sleep(DELAY)

    # Final hash check
    if not await compare_either_hash(file_path, firmware):
        logger.warning(f"Hash mismatch for {file_path}")

    return Ok(file_path)

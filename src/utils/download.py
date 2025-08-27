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
    last_content_length: None | int
) -> Result[aiohttp.ClientResponse, str]:
    file_size = file_path.stat().st_size if file_path.exists() else 0
    headers = {}

    if last_content_length is not None and file_size >= last_content_length:
        logger.warning(f"Local file bigger or equal than server file, local: {file_size}, server: {last_content_length}")

        return Error("already good")

    if file_size > 0:
        headers["Range"] = f"bytes={file_size}"
        msg = f"resuming download from byte {file_size}"

        if last_content_length is not None:
            msg += f", last remote content length: {last_content_length}"

        logger.info(msg)

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

    last_content_length: int | None = None

    # retry resuming the download if error ocurred
    for attempt in range(1, MAX_RETRIES + 1):

        response = await get_response(firmware, session, ignored_firmwares_file, git_mode, file_path, last_content_length)

        if isinstance(response, Error):
            if "416" in response.error:
                logger.warning("Got 416, deleting partial file and starting fresh")
                await cleanup_file(file_path)
                last_content_length = None
                continue

            if response.error == "already good":
                return Ok(file_path)

            await cleanup_file(file_path)
            return response

        response = response.value

        # Determine the total size for progress tracking
        if "Content-Range" in response.headers:
            last_content_length = int(response.headers["Content-Range"].split("/")[-1])
        else:
            last_content_length = int(response.headers.get("Content-Length", 0))

        if last_content_length == 0:
            logger.warning(f"No Content-Length available for {firmware.url}")

        try:
            await write_with_progress(response, file_path, last_content_length, CHUNK_SIZE)

            break
        except Exception as e:
            
            logger.warning(f"Downloading failed with: {e}")
            logger.warning(f"Last Content Length: {last_content_length}")
            logger.warning(f"File Size: {file_path.stat().st_size or '?'}")

            logger.warning("Retrying..")
            if attempt == MAX_RETRIES:
                await cleanup_file(file_path)

                return Error(f"Error writing file after {MAX_RETRIES} retries: {e}")

            await asyncio.sleep(DELAY)

    # Final hash check
    if not await compare_either_hash(file_path, firmware):
        logger.warning(f"Hash mismatch for {file_path}")

    return Ok(file_path)

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

MAX_RETRIES = 5
DELAY = 1
CHUNK_SIZE = 8_192

async def get_remote_file_size(session: aiohttp.ClientSession, url: str) -> int:
    async with session.head(url) as response:
        return int(response.headers.get("Content-Length", 0))


async def get_response(
    firmware: Firmware,
    session: aiohttp.ClientSession,
    ignored_firmwares_file: Path,
    git_mode: bool,
    file_path: Path,
    remote_file_size: int
) -> Result[aiohttp.ClientResponse, str]:
    file_size = file_path.stat().st_size if file_path.exists() else 0
    headers = {}


    if remote_file_size is not None and file_size >= remote_file_size:
        logger.warning(f"Local file bigger or equal than server file, local: {file_size}, server: {remote_file_size}")

        return Error("already good")

    if file_size > 0:
        headers["Range"] = f"bytes={file_size}-{remote_file_size - 1}"

        logger.info(f"resuming download from byte {file_size}, remote file size: {remote_file_size}")

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

    remote_file_size = await get_remote_file_size(session, firmware.url)

    # retry resuming the download if error ocurred
    for attempt in range(1, MAX_RETRIES + 1):

        response = await get_response(firmware, session, ignored_firmwares_file, git_mode, file_path, remote_file_size)

        if isinstance(response, Error):
            if "416" in response.error:
                logger.warning("Got 416, deleting partial file and starting fresh")
                await cleanup_file(file_path)
                continue

            if response.error == "already good":
                return Ok(file_path)

            await cleanup_file(file_path)
            return response

        response = response.value

        try:
            await write_with_progress(response, file_path, remote_file_size, CHUNK_SIZE)

            break
        except Exception as e:
            
            logger.debug(f"Downloading failed with: {e}")
            logger.debug(f"Remote File Size: {remote_file_size}")
            logger.debug(f"File Size: {file_path.stat().st_size or '?'}")

            logger.debug("Retrying..")
            if attempt == MAX_RETRIES:
                await cleanup_file(file_path)

                return Error(f"Error writing file after {MAX_RETRIES} retries: {e}")

            await asyncio.sleep(DELAY)

        finally:
            response.close()

    # Final hash check
    if not await compare_either_hash(file_path, firmware):
        logger.warning(f"Hash mismatch for {file_path}")

    return Ok(file_path)

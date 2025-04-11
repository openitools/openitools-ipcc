import asyncio
import plistlib
import zipfile
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

from models import Error, Ok, Result

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}


def _find_key_in_plist(plist_data, target_key: str) -> Result[str, None]:
    if isinstance(plist_data, dict):
        for key, value in plist_data.items():
            if key == target_key:
                return Ok(value)

            elif isinstance(value, (dict, list)):
                result = _find_key_in_plist(value, target_key)
                if isinstance(result, Ok):
                    return result

    elif isinstance(plist_data, list):
        for item in plist_data:
            if isinstance(item, (dict, list)):
                result = _find_key_in_plist(item, target_key)
                if isinstance(result, Ok):
                    return result

    return Error(None)


async def _fetch_key(
    build_train: str, build_id: str, identifier: str
) -> Result[str, str]:
    async with aiohttp.ClientSession() as session:
        html = await _fetch_html(
            session,
            f"https://theapplewiki.com/wiki/Keys:{build_train}_{build_id}_({identifier})",
        )

        if isinstance(html, Error):
            return Error(f"Unable to scrape the website, error: {html}")

        key = await _extract_key_from_html(html.value)

        if isinstance(key, Error):
            return Error("Unable to find the key")

        return key


async def decrypt_dmg(
    ipsw_file: Path, dmg_file: Path, build_id: str, identifier: str
) -> Result[None, str]:
    with zipfile.ZipFile(ipsw_file) as zip_file:
        with zip_file.open("BuildManifest.plist") as bmplist:
            plist_data = plistlib.loads(bmplist.read())
            build_train = _find_key_in_plist(plist_data, "BuildTrain")

            if isinstance(build_train, Error):
                return Error("BuildTrain was not found in the BuildManifest.plist file")

    key = await _fetch_key(build_train.value, build_id, identifier)

    if isinstance(key, Error):
        return key

    return await _extract_encrypted_dmg(dmg_file, key.value)


async def _extract_encrypted_dmg(dmg_file: Path, key: str) -> Result[None, str]:
    temp_file = dmg_file.parent / (dmg_file.name + ".temp")

    process = await asyncio.create_subprocess_exec(
        "vfdecrypt",
        "-i",
        str(dmg_file),
        "-k",
        key,
        "-o",
        str(temp_file),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        return Error(str(stderr.strip()))

    # Delete the old file and rename the temporary file to the original name
    try:
        dmg_file.unlink(missing_ok=True)
    except Exception as e:
        return Error(f"Failed to delete original file: {e}")

    try:
        temp_file.rename(dmg_file)
    except Exception as e:
        return Error(f"Failed to rename temporary file: {e}")

    return Ok(None)


async def _fetch_html(session, url) -> Result[str, str]:
    try:
        async with session.get(url, headers=HEADERS, timeout=30) as response:
            text: str = await response.text()

            if response.status == 200:
                return Ok(text)
            else:
                return Error(
                    f"Failed to fetch {url}: HTTP {response.status}, error: {text}"
                )
    except Exception as e:
        return Error(str(e))


async def _extract_key_from_html(html: str) -> Result[str, None]:
    soup = BeautifulSoup(html, "html.parser")
    code_tag = soup.find("code", id="keypage-rootfs-key")
    if code_tag:
        key = code_tag.text.strip()
        return Ok(key)
    else:
        return Error(None)

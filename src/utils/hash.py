import hashlib
from pathlib import Path
from typing import Literal

from models import Firmware


async def calculate_hash(file_path: Path, algo: Literal["sha1", "md5"]) -> str:
    hash_func = getattr(hashlib, algo)()
    
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 10000)
            if not chunk:
                break
            hash_func.update(chunk)

    return hash_func.hexdigest()

async def compare_either_hash(file_path: Path, firmware: Firmware) -> bool:
    sha1 = await calculate_hash(file_path, "sha1")

    if sha1.strip() == firmware.sha1sum.strip():
        return True
    
    md5 = await calculate_hash(file_path, "md5")

    if md5.strip() == firmware.md5sum.strip():
        return True

    return False

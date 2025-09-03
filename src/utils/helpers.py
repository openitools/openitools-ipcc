import asyncio
from pathlib import Path

from models import Error
from utils import logger
from utils.shell import run_command

INSTALL_LOCK = asyncio.Lock()
install_done = asyncio.Event()  # signals when install is finished


async def install_ipsw():
    if install_done.is_set():
        return

    async with INSTALL_LOCK:
        # Check again in case another coroutine finished while we were waiting
        if install_done.is_set():
            return

        deb_path = Path("ipsw.deb")

        if not deb_path.exists():
            logger.info("Downloading ipsw...")

            command = f"wget https://github.com/blacktop/ipsw/releases/download/v3.1.544/ipsw_3.1.544_linux_x86_64.deb -O {deb_path}"

            stdout, stderr, return_code = await run_command(command)

            if return_code != 0:
                return Error(f"Failed to download ipsw: {stdout} | {stderr}")

        stdout, stderr, return_code = await run_command(f"sudo dpkg -i {deb_path}")

        if return_code != 0:
            return Error(f"Failed to install ipsw: {stdout} | {stderr}")

        deb_path.unlink(missing_ok=True)

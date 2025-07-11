from pathlib import Path

from models import Error
from utils import logger
from utils.shell import run_command


async def install_ipsw():
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

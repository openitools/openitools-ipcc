import asyncio
from pathlib import Path

import aiofiles

from models import Firmware
from utils import logger
from utils.fs import put_metadata
from utils.shell import run_command

# only one can upload and use git
GIT_LOCK = asyncio.Lock()


async def process_files_with_git(
        firmware: Firmware, message: str = "added {version} ipcc files for {ident}", sparse_checkout_path: str | None = None, add_path: str | None = None
):
    logger.debug("waiting for the git lock")
    async with GIT_LOCK:
        # allow the will be pushed dir
        if sparse_checkout_path:
            await run_command(f"git sparse-checkout add '{sparse_checkout_path.format(ident=firmware.identifier, version=firmware.version)}'")
        else:
            await run_command(f"git sparse-checkout add '{firmware.identifier}/{firmware.version}/*'")

        await run_command(f"git add {add_path if add_path else firmware.identifier}")

        # await run_command("git stash push")
        #
        # await run_command("git switch -f files")
        #
        # # we don't want to check for the pop, because there might be conflicts in there, which will be resolved in the next command
        # await run_command("git stash pop", check=False)
        #
        #
        # out, *_ = await run_command(
        #     "git diff --name-only --diff-filter=U",
        # )
        #
        # for path in out.splitlines():
        #     path = path.strip()
        #     if not path:
        #         continue
        #     await run_command(f"git checkout --theirs {path}")
        #
        #     await run_command(f"git add {path}")

        await run_command(
            f"git commit -m '{message.format(version=firmware.version, ident=firmware.identifier)}'"
        )

        await run_command("git push origin files")
        # await run_command("git switch main")

async def ignore_firmware(ignored_firmwares_file: Path, firmware: Firmware, was_it_retrying: bool):
    await put_metadata(
            ignored_firmwares_file,
            "ignored",
            lambda ign: (ign or []) + [firmware.version],
    )

    # so if it's retrying, no need to ignore the firmware
    # FIXME: janky, no good
    if not was_it_retrying:
        await process_files_with_git(firmware, message="Ignored {version} for {ident}", sparse_checkout_path=f"{ignored_firmwares_file}")

async def check_file_existence_in_branch(branch: str, file_path: str) -> bool:
    try:
        result, *_ = await run_command(
            f"git ls-tree -r --name-only {branch} -- {file_path}"
        )
    except Exception:
        # attempt to recover from wrong branch
        await run_command("git switch files")
        await run_command("git switch main")
        return await check_file_existence_in_branch(branch, file_path)

    return bool(result.strip())


async def copy_previous_metadata(ident: str) -> None:
    ignored_firms_file_path = f"{ident}/ignored_firmwares.json"
    metadata_file_path = f"{ident}/metadata.json"

    command = lambda file_path: f"git show files:{file_path}"

    Path(ident).mkdir(exist_ok=True)

    if await check_file_existence_in_branch("files", ignored_firms_file_path):
        stdout, _, _ = await run_command(command(ignored_firms_file_path))
        async with aiofiles.open(ignored_firms_file_path, "w") as f:
            await f.write(stdout)

    if await check_file_existence_in_branch("files", metadata_file_path):
        stdout, _, _ = await run_command(command(metadata_file_path))
        async with aiofiles.open(metadata_file_path, "w") as f:
            await f.write(stdout)

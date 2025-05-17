import asyncio
from pathlib import Path

import aiofiles

from utils import logger
from utils.shell import run_command

# only one can upload and use git
GIT_LOCK = asyncio.Lock()


async def process_files_with_git(ident: str, version: str):
    logger.debug("waiting for the git lock")
    async with GIT_LOCK:
        await run_command(f"git add {ident}")

        await run_command("git stash push")

        await run_command("git switch files")

        # we don't want to check for the pop, because there might be conflicts in there, which will be resolved in the next command
        await run_command("git stash pop", check=False)


        out, *_ = await run_command(
            "git diff --name-only --diff-filter=U",
        )

        for path in out.splitlines():
            path = path.strip()
            if not path:
                continue
            await run_command(f"git checkout --theirs {path}")
                
            await run_command(f"git add {path}")

        await run_command(f"git commit -m 'added {version} ipcc files for {ident}'")

        await run_command("git push origin files")

        await run_command("git switch main")


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

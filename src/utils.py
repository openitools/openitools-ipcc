import asyncio
import glob
import hashlib
import json
import logging
import shlex
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Callable, List, Literal, Optional, TypeVar, Union

import aiofiles

from models import Error, Firmware, Ok, Result

logger = logging.getLogger()

# for json writing callbacks
J = TypeVar("J")

# only one can upload and use git
git_lock = asyncio.Lock()

async def run_command(command: Union[str, list[str]], check: bool = True, stdout: int | IO[Any] = asyncio.subprocess.PIPE) -> tuple[str, str, Optional[int]]:

    if isinstance(command, str):
        command = shlex.split(command)

    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=stdout,
        stderr=asyncio.subprocess.PIPE,
    )

    result_stdout, result_stderr = await proc.communicate()

    result_stdout = result_stdout.decode() if isinstance(result_stdout, (bytes, bytearray)) else ""
    result_stderr = result_stderr.decode() if isinstance(result_stderr, (bytes, bytearray)) else ""

    if proc.returncode != 0 and check:
        raise RuntimeError(f"Command `{command}` failed with code {proc.returncode}:\n{result_stderr}")

    return result_stdout, result_stderr, proc.returncode

async def process_files_with_git(ident: str, version: str):
    logger.debug("waiting for the git lock")

    async with git_lock:
        out, err, _ = await run_command(f"git add {ident}")

        logger.debug(f"git add {ident} output: \n stdout: {out} \nstderr: {err}")

        out, err, _ = await run_command("git stash push")

        logger.debug(f"git stash push output: \n stdout: {out} \nstderr: {err}")

        out, err, _ = await run_command("git switch -f files")

        logger.debug(f"git switch -f files output: \n stdout: {out} \nstderr: {err}")

        # we don't want to check for the pop, because there might be conflicts in there, which will be resolved in the next command
        out, err, _ = await run_command("git stash pop", check=False)


        logger.debug(f"git stash pop output: \n stdout: {out} \nstderr: {err}")

        out, err , _ = await run_command(
            "git diff --name-only --diff-filter=U",
        )

        logger.debug(f"git diff output: \n stdout: {out} \nstderr: {err}")

        for path in out.splitlines():
            out, err, _ = await run_command(f"git checkout --theirs {path}")
            logger.debug(f"git checkout --theirs {path} output: \n stdout: {out} \nstderr: {err}")

        out, err, _ = await run_command("git add .")

        logger.debug(f"git add {ident} output: \n stdout: {out} \nstderr: {err}")



        out, err, _ = await run_command(f"git commit -m 'added {version} ipcc files for {ident}'")


        logger.debug(f"git add . output: \n stdout: {out} \nstderr: {err}")

        out, err, _ = await run_command("git push origin files")


        logger.debug(f"git push origin files output: \n stdout: {out} \nstderr: {err}")
        out, err, _ = await run_command("git switch main")

        logger.debug(f"git switch main output: \n stdout: {out} \nstderr: {err}")


async def check_file_existence_in_branch(branch: str, file_path: str) -> bool:
    try:
        result = await run_command(
            f"git ls-tree -r --name-only {branch} -- {file_path}"
        )
    except Exception:
        await run_command("git switch files")
        await run_command("git switch main")
        return await check_file_existence_in_branch(branch, file_path)

    return bool(result[0].strip())


async def copy_previous_metadata(ident: str) -> None:
    ignored_firms_file_path = f"{ident}/ignored_firmwares.json"
    ignored_firms_exists = await check_file_existence_in_branch(
        "files", ignored_firms_file_path
    )

    metadata_file_path = f"{ident}/metadata.json"
    metadata_exists = await check_file_existence_in_branch("files", metadata_file_path)

    command = lambda file_path: f"git show files:{file_path}"

    Path(ident).mkdir(exist_ok=True)

    if ignored_firms_exists:
        with open(ignored_firms_file_path, "w") as f:
            await run_command(
                command(ignored_firms_file_path), stdout=f
            )

    if metadata_exists:
        with open(metadata_file_path, "w") as f:
            await run_command(
                command(metadata_file_path), stdout=f
            )


async def calculate_hash(file_path: Path, algo: Literal["sha1", "md5"]) -> str:
    hash_func = getattr(hashlib, algo)()
    
    async with aiofiles.open(file_path, "rb") as f:
        while True:
            chunk = await f.read(1024 * 10000)
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


async def put_metadata(
    metadata_path: Path, key: str, callback: Callable[[Optional[J]], J]
) -> Result[None, str]:
    """Read & update JSON metadata using a callback."""
    try:
        async with aiofiles.open(metadata_path, "r") as f:
            try:
                text = await f.read()
                metadata = json.loads(text) if text.strip() else {}
            except FileNotFoundError:
                metadata = {}

        metadata[key] = callback(metadata.get(key))
        metadata["updated_at"] = datetime.now(UTC).isoformat()

        async with aiofiles.open(metadata_path, "w") as f:
            await f.write(json.dumps(metadata, indent=4))

        return Ok(None)

    except json.JSONDecodeError as e:
        return Error(f"Invalid JSON: {e}")
    except Exception as e:
        return Error(f"Unexpected error: {e}")


def bundles_glob(path: Path, has_parent: bool = False) -> List[Path]:
    return list(
        map(
            lambda s: Path(s).resolve(),
            glob.glob(
                f"{path}/{'*/' if has_parent else ''}System/Library/Carrier Bundles/**/*.bundle"
            ),
        )
    )


def delete_non_bundles(
    base_path: Path, bundles: List[Path], has_parent: bool = False
) -> Result[List[Path], str]:
    try:
        for bundle in bundles:
            shutil.move(bundle, base_path / bundle.name)

        if has_parent:
            system_dirs = list(base_path.glob("*/System"))
            if not system_dirs:
                return Error("No nested 'System' folder found")
            path = system_dirs[0].parent
        else:
            path = base_path / "System"

        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

        return Ok([base_path / bundle.name for bundle in bundles])

    except Exception as e:
        return Error(f"Failed to clean up: {e}")


async def system_has_parent(dmg_file: Path) -> Result[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        "7z",
        "l",
        dmg_file,
        "*/System",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if proc.stdout is None or proc.stderr is None:
        return Error("Failed to capture stdout/stderr from 7z")

    lines = set()

    start_collecting = False

    while True:
        # TODO: sometimes it freezes

        # stderr_data = await proc.stderr.read()
        #
        # if stderr_data:
        #     return Error(stderr_data.decode(errors="ignore"))

        line = await proc.stdout.readline()

        if line == b"":
            break

        if b"Date" in line and b"Time" in line:
            start_collecting = True

        if start_collecting:
            lines.add(line)

            if len(lines) > 10:
                break

    try:
        proc.kill()
    except ProcessLookupError:
        pass

    return Ok(len(lines) > 10)

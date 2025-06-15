import asyncio
import shlex
from typing import IO, Any, List, Optional, Union

from utils import logger


async def run_command(
    command: Union[str, List[str]],
    check: bool = True,
    stdout: int | IO[Any] = asyncio.subprocess.PIPE,
) -> tuple[str, str, Optional[int]]:
    if isinstance(command, str):
        command = shlex.split(command)

    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=stdout,
        stderr=asyncio.subprocess.PIPE,
    )

    result_stdout, result_stderr = await proc.communicate()

    result_stdout = (
        result_stdout.decode() if isinstance(result_stdout, (bytes, bytearray)) else ""
    )
    result_stderr = (
        result_stderr.decode() if isinstance(result_stderr, (bytes, bytearray)) else ""
    )

    logger.debug(
        f"{' '.join(map(str, command))} output: \n stdout: {result_stdout}\n stderr: {result_stderr}"
    )

    if proc.returncode != 0 and check:
        raise RuntimeError(
            f"Command `{command}` failed with code {proc.returncode}:\n{result_stderr}"
        )

    return result_stdout, result_stderr, proc.returncode

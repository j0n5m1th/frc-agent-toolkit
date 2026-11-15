"""Run an installed CTRE tool with fixed arguments: no shell, no stdin, a time limit, and bounded, cleaned output.

CTRE tools print progress bars as `Progress: [ 12%] [#####.....]` repeated on one line; `clean_output` drops them so a result carries
only the words. The child is killed when the time limit passes.
"""

import asyncio
import contextlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from frc_mcp_core.processes import (
    KILL_TIMEOUT_S,
    forget_child,
    kill_tree_sync,
    new_session_kwargs,
    remember_child,
    track_child,
)

from .errors import CtreError

_PROGRESS = re.compile(r"Progress:\s*\[\s*\d+%\]\s*\[[#. ]*\]\s*")
KEEP_BYTES = 8 * 2**20


@dataclass(frozen=True)
class RunResult:
    returncode: int
    output: str  # cleaned text, stdout and stderr together
    truncated: bool


def clean_output(text: str) -> str:
    """Text without progress bars, carriage returns or blank lines."""
    text = _PROGRESS.sub("", text.replace("\r", "\n"))
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


async def run_tool(exe: Path, args: list[str], timeout_s: float) -> RunResult:
    """Run `exe args` and wait. Raises CtreError when it cannot start or runs longer than `timeout_s` (the child is then killed).

    The child is an asyncio subprocess, so cancelling this call (or the time limit) kills the child and its own children
    instead of leaving them running; on Windows it also joins the job that ends it if the server dies.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            str(exe), *args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **new_session_kwargs())
    except OSError as e:
        raise CtreError(f"Could not start {exe.name}: {e}") from e
    track_child(proc.pid)
    remember_child(proc.pid)  # a termination signal kills it too
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError as e:
        await _stop(proc)
        raise CtreError(f"{exe.name} did not finish within {timeout_s:g} s and was stopped.") from e
    except BaseException:  # includes CancelledError: the caller gave up, so the child must not outlive the call
        await _stop(proc)
        raise
    finally:
        forget_child(proc.pid)
    return RunResult(proc.returncode or 0, clean_output(raw[:KEEP_BYTES].decode("utf-8", errors="replace")), len(raw) > KEEP_BYTES)


async def _stop(proc: asyncio.subprocess.Process) -> None:
    """Kill the child's whole tree, then reap it. The kill is synchronous so a second cancellation cannot skip it."""
    kill_tree_sync(proc.pid)
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await asyncio.wait_for(proc.wait(), KILL_TIMEOUT_S)

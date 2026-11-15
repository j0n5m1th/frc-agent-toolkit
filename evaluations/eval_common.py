"""Small helpers shared by the evaluation scripts (paths, number checks, fake-robot process, atomic writes)."""

import contextlib
import math
import os
import sys
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from subprocess import DEVNULL, Popen, TimeoutExpired
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
FAKE_ROBOT_SCRIPT = ROOT / "evaluations" / "fake_robot.py"
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
SERVER = "nt4"
LOG_SERVER = "wpilog"  # the log server's key: its name in the MCP config, and so in its tool names (mcp__wpilog__*)
MIN_PORT, MAX_PORT = 1, 65535
SCHEMA_VERSION = 2  # evaluation report format shared by the runner and the comparer
STOP_TIMEOUT_S = 10  # how long a terminated child gets before it is killed


def number(value: object) -> float | int | None:
    """The value if it is a nonnegative finite int/float (not bool), else None."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def valid_tool_call_counts(calls: object) -> bool:
    """True for None or a mapping of nonempty tool names to nonnegative integer counts."""
    if calls is None:
        return True
    if not isinstance(calls, Mapping):
        return False
    return all(
        isinstance(name, str) and name and isinstance(count, int) and not isinstance(count, bool) and count >= 0
        for name, count in calls.items()
    )


def reject_constant(value: str) -> NoReturn:
    """json.loads(parse_constant=...) hook: refuse NaN and Infinity."""
    raise ValueError(f"non-finite JSON value: {value}")


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write via a temporary sibling file and os.replace so readers never see a half-written file."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _spawn_fake_robot(port: int, extra_args: Iterable[str]) -> Popen:
    argv = [sys.executable, str(FAKE_ROBOT_SCRIPT), "--port", str(port), *extra_args]
    return Popen(  # nosec B603 - fixed argv list, no shell, no untrusted input
        argv, stdout=DEVNULL, stderr=DEVNULL,
    )


def stop_process(proc: Popen, timeout: float = STOP_TIMEOUT_S) -> None:
    """Terminate a child, escalating to kill if it does not exit within `timeout` seconds."""
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)


@contextlib.contextmanager
def fake_robot_process(port: int, extra_args: Iterable[str] = ()) -> Iterator[Popen]:
    """Run evaluations/fake_robot.py on `port` (output discarded) for the with-block, then stop it.

    The child is a long-running server, so it cannot be bounded by a subprocess timeout; instead the
    guaranteed shutdown below terminates it and kills it after STOP_TIMEOUT_S, and callers wait for
    readiness with their own deadline while the block is open.
    """
    proc = _spawn_fake_robot(port, extra_args)
    try:
        yield proc
    finally:
        stop_process(proc)

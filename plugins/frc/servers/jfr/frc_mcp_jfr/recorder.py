"""Recordings that are still being made (started with `wait=false`): so the other tools can say "not ready yet" instead of "no such file"."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import JfrError

GRACE_S = 5.0  # after the recording ends, JFR still needs a moment to write the file


@dataclass(frozen=True)
class Pending:
    pid: int
    path: Path
    ends_at: float  # time.monotonic()


_pending: dict[str, Pending] = {}


def start(path: Path, pid: int, seconds: float) -> None:
    _pending[path.name] = Pending(pid, path, time.monotonic() + seconds)


def _ready(p: Pending) -> bool:
    return p.path.is_file() and p.path.stat().st_size > 0 and time.monotonic() > p.ends_at


def _forget_finished() -> None:
    now = time.monotonic()
    for name, p in list(_pending.items()):
        if _ready(p) or now > p.ends_at + GRACE_S:
            del _pending[name]


def active() -> list[dict[str, Any]]:
    """Recordings not finished yet, with the seconds left."""
    _forget_finished()
    return [{"file": name, "recording_s_left": max(round(p.ends_at - time.monotonic()), 0)} for name, p in _pending.items()]


def check_ready(file: str) -> None:
    """Raise a JfrError saying how long to wait if `file` is a recording that has not finished."""
    _forget_finished()
    if p := _pending.get(Path(file).name):
        raise JfrError(f"{p.path.name} is still being recorded (about {max(p.ends_at - time.monotonic(), 1):.0f} s left). Try again after that.")

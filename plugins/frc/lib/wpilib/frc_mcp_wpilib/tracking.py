"""Compare a measured signal with the command it was following: the part every tracking check shares (swerve modules, a mechanism, a pose).

A command holds until the next command, so each measured update is paired with the last command at or before it. Only the updates inside the
time windows count, thinned to a bounded number so a long log costs no more than a short one.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from typing import Any

from .errors import WPILibMCPError
from .scan_rows import Row, Window, everywhere

MAX_SAMPLES = 3000  # measured updates read (evenly spread)
MIN_COMPARED = 10  # fewer paired updates than this say nothing about how well a signal followed its command

Samples = Sequence[Row]  # (time in microseconds, value), oldest first


class NothingToCompare(WPILibMCPError):
    """The two signals cannot be compared: one has no updates, none has a command before it, there are too few pairs, or the values are not the expected kind."""


def sampled(times: Sequence[int], windows: Sequence[Window]) -> list[int]:
    """Row numbers inside the windows, thinned to at most `MAX_SAMPLES` evenly spread. Rows are in time order, so each window is a slice."""
    spans = [range(bisect_left(times, lo), bisect_right(times, hi)) for lo, hi in everywhere(windows)]
    step = max(1, sum(len(r) for r in spans) // MAX_SAMPLES)
    return [i for r in spans for i in r[::step]]


def held_pairs(measured: Samples, command: Samples, windows: Sequence[Window]) -> Iterator[tuple[int, Any, int, Any]]:
    """`(time, measured value, command row number, command value)` for each measured update in the windows that has a command before it.
    The command's row number lets a caller decode a command once while it holds. Raises NothingToCompare when either signal has no updates."""
    if not measured or not command:
        raise NothingToCompare("the measured or the commanded signal has no updates")
    command_times = [t for t, _ in command]
    for i in sampled([t for t, _ in measured], windows):
        t, value = measured[i]
        if (j := bisect_right(command_times, t) - 1) >= 0:
            yield t, value, j, command[j][1]


def require_enough(compared: int) -> None:
    """Raise NothingToCompare unless at least `MIN_COMPARED` updates were paired."""
    if compared == 0:
        raise NothingToCompare("no measured update has a commanded one before it")
    if compared < MIN_COMPARED:
        raise NothingToCompare(f"only {compared} measured update(s) could be compared (at least {MIN_COMPARED} are needed)")

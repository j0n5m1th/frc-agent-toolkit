"""Compare a measured signal with the command it was following: the part every tracking check shares (swerve modules, a mechanism, a pose).

A command holds until the next command, so each measured update is paired with the last command at or before it. Only the updates inside the
time windows count, every one of them: a worst moment is a single update, so nothing is thinned out.
"""

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from typing import Any

from .errors import WPILibMCPError
from .reduce.stats import US_PER_S
from .scan_rows import Row, Window, everywhere

MIN_COMPARED = 10  # fewer paired updates than this say nothing about how well a signal followed its command

Samples = Sequence[Row]  # (time in microseconds, value), oldest first


class NothingToCompare(WPILibMCPError):
    """The two signals cannot be compared: one has no updates, none has a command before it, there are too few pairs, or the values are not the expected kind."""


def seconds_since(us: int, origin_us: int) -> float:
    """Seconds from time zero to `us`, to a tenth."""
    return round((us - origin_us) / US_PER_S, 1)


def turn_deg(radians: float) -> float:
    """The size, in degrees, of a turn or an angle difference of `radians`, taking the short way round."""
    return abs((math.degrees(radians) + 180) % 360 - 180)


def held_pairs(measured: Samples, command: Samples, windows: Sequence[Window]) -> Iterator[tuple[int, Any, int, Any]]:
    """`(time, measured value, command row number, command value)` for each measured update in the windows that has a command before it.
    The command's row number lets a caller decode a command once while it holds. Raises NothingToCompare when either signal has no updates."""
    if not measured or not command:
        raise NothingToCompare("the measured or the commanded signal has no updates")
    command_times = [t for t, _ in command]
    times = [t for t, _ in measured]
    for lo, hi in everywhere(windows):
        for i in range(bisect_left(times, lo), bisect_right(times, hi)):
            t, value = measured[i]
            if (j := bisect_right(command_times, t) - 1) >= 0:
                yield t, value, j, command[j][1]


def require_enough(compared: int) -> None:
    """Raise NothingToCompare unless at least `MIN_COMPARED` updates were paired."""
    if compared == 0:
        raise NothingToCompare("no measured update has a commanded one before it")
    if compared < MIN_COMPARED:
        raise NothingToCompare(f"only {compared} measured update(s) could be compared (at least {MIN_COMPARED} are needed)")

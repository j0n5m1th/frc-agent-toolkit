"""The row type a scan reads, and the filter every numeric check needs."""

import math
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from typing import Any

Row = tuple[int, Any]  # (time in microseconds, value)
Window = tuple[int, int]  # (start, end) in microseconds, inclusive


def finite_numbers(rows: Iterable[Row]) -> list[Row]:
    """The rows whose value is a finite number (no text, no NaN or infinity), which a sort, a max or a percentile can take."""
    return [r for r in rows if isinstance(r[1], (int, float)) and math.isfinite(float(r[1]))]


def within(rows: Iterable[Row], windows: Sequence[Window]) -> list[Row]:
    """The rows whose time is inside one of the windows (all rows when there are no windows)."""
    if not windows:
        return list(rows)
    return [r for r in rows if any(lo <= r[0] <= hi for lo, hi in windows)]


def value_at(rows: Sequence[Row], t: int) -> Any:
    """The value a signal held at time `t` (its last update at or before it), or None before its first update. Rows are in time order."""
    i = bisect_right(rows, t, key=lambda r: r[0]) - 1
    return rows[i][1] if i >= 0 else None


def everywhere(windows: Sequence[Window]) -> Sequence[Window]:
    """The windows given, or all of time when there are none (a live recording has no enabled periods to look inside)."""
    return windows or [(-(2**62), 2**62)]

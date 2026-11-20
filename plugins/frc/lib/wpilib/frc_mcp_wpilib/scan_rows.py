"""The row type a scan reads, and the filter every numeric check needs."""

import math
from collections.abc import Iterable
from typing import Any

Row = tuple[int, Any]  # (time in microseconds, value)
Window = tuple[int, int]  # (start, end) in microseconds, inclusive


def finite_numbers(rows: Iterable[Row]) -> list[Row]:
    """The rows whose value is a finite number (no text, no NaN or infinity), which a sort, a max or a percentile can take."""
    return [r for r in rows if isinstance(r[1], (int, float)) and math.isfinite(float(r[1]))]

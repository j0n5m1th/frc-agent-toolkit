"""The time window and alignment of a wpilog question, and the helpers that cut sorted rows to it. `Range` and `QuerySpec` are
what a question asks for; the rest are pure functions over rows sorted by time. `queries.py` re-exports every name.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from operator import itemgetter
from typing import Any

from frc_mcp_wpilib.reduce.stats import US_PER_MS
from frc_mcp_wpilib.tooling.query_bases import ReduceKnobs

from .rows import LogRow

_TIME = itemgetter(0)  # the time of a (time, value) row, as the sort key the binary searches below use
NOT_IN_LOG = "not in this log"
FIND_NAMES_HINT = "wpilog_topics name_contains=... finds names"  # said once per reply, not per missing topic


@dataclass(frozen=True)
class Range:
    """The time window and alignment a question is about: `since_ms`/`until_ms` plus `align` ("start", "auto" or "end")."""

    align: str = "start"
    since_ms: float | None = None
    until_ms: float | None = None


@dataclass(frozen=True)
class QuerySpec(ReduceKnobs):
    """The reduce knobs one `wpilog_query` question carries (all of `ReduceKnobs`); topic names and the `Range` window travel
    beside it.
    """


def aligned(rows: Sequence[LogRow], origin: int) -> list[tuple[float, Any]]:
    """Rows with their time in milliseconds since `origin_us`."""
    return [((t - origin) / US_PER_MS, v) for t, v in rows]


def _held_index(rows: Sequence[Any], since_ms: float | None, key: Callable[[Any], float] = _TIME) -> int:
    """How many rows come before the window (the newest of them is the one held when it opens); 0 without a start.

    Rows are sorted by time (`LogFile` guarantees it), so this is a binary search; `key` reads a row's time in ms."""
    return 0 if since_ms is None else bisect_left(rows, since_ms, key=key)


def _held_before(rows: list[tuple[float, Any]], since_ms: float | None) -> tuple[float, Any] | None:
    """The newest update before the window, which is still the topic's value when the window opens."""
    i = _held_index(rows, since_ms)
    return rows[i - 1] if i else None


def _window_bounds(rows: Sequence[Any], since_ms: float | None, until_ms: float | None,
                   key: Callable[[Any], float] = _TIME) -> tuple[int, int]:
    """The slice `rows[lo:hi]` of the rows with `since_ms <= time <= until_ms` (an open end is no bound); empty when
    `hi <= lo`. Binary searches over sorted rows, so no row outside the window is touched."""
    lo = 0 if since_ms is None else bisect_left(rows, since_ms, key=key)
    hi = len(rows) if until_ms is None else bisect_right(rows, until_ms, key=key)
    return lo, hi


def _in_window(rows: list[tuple[float, Any]], since_ms: float | None, until_ms: float | None) -> list[tuple[float, Any]]:
    if since_ms is None and until_ms is None:
        return rows
    lo, hi = _window_bounds(rows, since_ms, until_ms)
    return rows[lo:hi]


def _at_ms(t_us: int, origin: int) -> float:
    """A WPILOG timestamp as milliseconds after the aligned time zero."""
    return (t_us - origin) / US_PER_MS


def _in_range(t_ms: float, window: Range) -> bool:
    """Whether a millisecond time falls inside the window (an open end is no bound)."""
    return (window.since_ms is None or t_ms >= window.since_ms) and (window.until_ms is None or t_ms <= window.until_ms)

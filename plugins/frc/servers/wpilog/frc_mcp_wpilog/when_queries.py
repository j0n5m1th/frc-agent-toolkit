"""`when`: the stretches of time a condition held, or the times a value changed."""

import logging
import math
from bisect import bisect_right
from collections.abc import Sequence
from typing import Any, NamedTuple

from frc_mcp_wpilib.conditions import (
    CompareOp,
    ConditionIndeterminate,
    StructDecodePending,
    check_compare_target,
    extract,
    holds,
    values_equal,
)
from frc_mcp_wpilib.reduce.models import Condition
from frc_mcp_wpilib.reduce.stats import US_PER_MS
from frc_mcp_wpilib.values import DEFAULT_PRECISION, shape

from .errors import WPILogError
from .logfile import Topic, serialized
from .loglike import LogLike
from .rows import LogRow
from .timeline import origin_us
from .windows import (
    FIND_NAMES_HINT,
    NOT_IN_LOG,
    Range,
    _held_index,
    _window_bounds,
    aligned,
)

_log = logging.getLogger(__name__)


def _intervals(
    rows: list[tuple[float, Any]],
    topic: Topic,
    op: CompareOp,
    target: Any,
    field: str | None,
    end_ms: float,
) -> tuple[list[list[float]], bool]:
    """The stretches where the condition held, and whether it still held at the last update.

    Each update's value holds until the next one, so a true update opens an interval and the first false one closes it.
    """
    intervals: list[list[float]] = []
    open_from: float | None = None
    for t, raw in rows:
        try:
            now_true = holds(op, extract(topic.type_str, raw, field), target, None)
        except ConditionIndeterminate as e:
            _log.debug("%s: %s is indeterminate at %s ms: %s", topic.name, field or "value", t, e)
            continue  # a NaN update says nothing either way
        except StructDecodePending as e:
            raise WPILogError(f"{e} (this log has no usable schema for it)") from e
        if now_true and open_from is None:
            open_from = t
        elif not now_true and open_from is not None:
            intervals.append([open_from, t])
            open_from = None
    still_true = open_from is not None
    if open_from is not None:
        intervals.append([open_from, max(end_ms, open_from)])
    return intervals, still_true


def _window_rows(log: LogLike, name: str, window: Range, origin: int) -> list[tuple[float, Any]]:
    """A topic's rows for a window question: its updates inside the window, after the value it held when the window began."""
    rows = log.rows([name])[name]

    def ms(row: LogRow) -> float:  # the same conversion `aligned` does, so the window cuts the rows exactly where it would
        return (row[0] - origin) / US_PER_MS

    start_ms = window.since_ms if window.since_ms is not None else (log.start_us - origin) / US_PER_MS
    held = _held_index(rows, window.since_ms, ms)  # only the rows the question needs are converted, not the whole topic
    lo, hi = _window_bounds(rows, window.since_ms, window.until_ms, ms)
    return ([(start_ms, rows[held - 1][1])] if held else []) + aligned(rows[lo:hi], origin)


def _intersect(first: list[list[float]], second: list[list[float]], end_ms: float) -> list[list[float]]:
    """The stretches inside both lists of sorted intervals. Each interval holds from its start until the next change, so
    two that merely touch overlap for no time; only a single instant at the very end of the window (where both are
    still true on the last update) counts."""
    both: list[list[float]] = []
    i = j = 0
    while i < len(first) and j < len(second):
        start, end = max(first[i][0], second[j][0]), min(first[i][1], second[j][1])
        if start < end or start == end == end_ms:
            both.append([start, end])
        if first[i][1] < second[j][1]:
            i += 1
        else:
            j += 1
    return both


class _Clause(NamedTuple):
    """One comparison of a `when` question: the main one (any op) or an `also` one (a state op)."""

    topic: str
    op: CompareOp
    value: Any
    field: str | None


@serialized
def when(
    log: LogLike,
    name: str,
    op: CompareOp,
    target: Any,
    field: str | None,
    window: Range,
    max_intervals: int,
    also: Sequence[Condition] = (),
    sample: Sequence[str] = (),
) -> dict[str, Any]:
    """The stretches of time a condition on one topic was true (each update's value holds until the next).
    With `also`, only the stretches when every condition held at once. With op `changed` it returns the change times instead
    (not combinable with `also`). Raises WPILogError for an unknown topic or a condition that cannot be evaluated.
    """
    clauses = [_Clause(name, op, target, field), *(_Clause(c.topic, c.op, c.value, c.field) for c in also)]
    for clause in clauses:
        if clause.topic not in log.topics:
            raise WPILogError(f"{clause.topic!r} is {NOT_IN_LOG} ({FIND_NAMES_HINT}).")
        if (why := log.topics[clause.topic].unusable_for("`when`")) is not None:
            raise WPILogError(why)
    check_compare_target(op, target)
    if op == "changed" and also:
        raise WPILogError("op 'changed' lists the changes of one topic; it cannot be combined with `also`.")
    origin, _ = origin_us(log, window.align)
    log.activate_schemas()
    log.rows([c.topic for c in clauses])  # one pass over the file for every topic the clauses read (the reads below hit the cache)
    log_end_ms = (log.end_us - origin) / US_PER_MS
    if window.since_ms is not None and window.since_ms > log_end_ms:  # the window opens after the last record: nothing was recorded
        empty = {"n": 0, "changes_ms": []} if op == "changed" else {"n": 0, "total_ms": 0.0, "intervals_ms": []}
        return _with_samples(log, empty, [], sample, origin)  # still checks the names
    end_ms = log_end_ms if window.until_ms is None else min(window.until_ms, log_end_ms)  # a value holds only as long as the log
    if op == "changed":
        changed = _changes(_window_rows(log, name, window, origin), max_intervals)
        return _with_samples(log, changed, changed["changes_ms"], sample, origin)
    per_condition = [
        _intervals(_window_rows(log, c.topic, window, origin), log.topics[c.topic], c.op, c.value, c.field, end_ms)
        for c in clauses
    ]
    intervals, still_true = per_condition[0]
    for other, other_true in per_condition[1:]:
        intervals, still_true = _intersect(intervals, other, end_ms), still_true and other_true
    total = math.fsum(end - start for start, end in intervals)
    out: dict[str, Any] = {"n": len(intervals), "total_ms": round(total, 1)}
    if intervals:
        out["longest_ms"] = round(max(end - start for start, end in intervals), 1)
    shown = intervals[:max_intervals]
    out["intervals_ms"] = [[round(s, 1), round(e, 1)] for s, e in shown]
    if len(intervals) > len(shown):
        out["omitted"] = len(intervals) - len(shown)
    if still_true:
        out["true_at_end"] = True
    return _with_samples(log, out, [s for s, _ in shown], sample, origin)


def _changes(rows: list[tuple[float, Any]], limit: int) -> dict[str, Any]:
    times = [t for (_, a), (t, b) in zip(rows, rows[1:], strict=False) if not values_equal(a, b)]
    out: dict[str, Any] = {"n": len(times), "changes_ms": [round(t, 1) for t in times[:limit]]}
    if len(times) > limit:
        out["omitted"] = len(times) - limit
    return out


def _with_samples(log: LogLike, out: dict[str, Any], times_ms: list[float], sample: Sequence[str], origin: int) -> dict[str, Any]:
    """Add `sampled`: for each topic in `sample`, its value at each listed time (parallel to the intervals' starts or the changes)."""
    if not sample:
        return out
    missing = [n for n in sample if n not in log.topics]
    if missing:
        raise WPILogError(f"{missing[0]!r} is {NOT_IN_LOG} ({FIND_NAMES_HINT}).")
    names = list(dict.fromkeys(sample))
    sampled: dict[str, Any] = {}
    for name, rows in log.rows(names).items():
        topic = log.topics[name]
        if (why := topic.unusable_for("`sample`")) is not None:
            raise WPILogError(why)
        stamps = [(t - origin) / US_PER_MS for t, _ in rows]
        values = []
        for at in times_ms:
            i = bisect_right(stamps, at)
            values.append(shape(rows[i - 1][1], topic.type_str, None, DEFAULT_PRECISION) if i else None)
        sampled[name] = values
    out["sampled"] = sampled
    return out

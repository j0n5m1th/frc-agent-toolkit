"""Reduced views of a log: `query` (one log), `compare` (several side by side) and `values_at` (a value at a time)."""

from bisect import bisect_right
from typing import Any

from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilib.reduce.segments import CAVEAT_FLAGS, Window
from frc_mcp_wpilib.reduce.shared import hoist_shared
from frc_mcp_wpilib.reduce.stats import US_PER_MS, Shaper, reduce_rows
from frc_mcp_wpilib.values import DEFAULT_PRECISION, shape, type_field

from .errors import WPILogError
from .logfile import Topic, serialized
from .loglike import LogLike
from .rows import LogRow, Rows
from .timeline import origin_us
from .windows import (
    _TIME,
    FIND_NAMES_HINT,
    NOT_IN_LOG,
    QuerySpec,
    Range,
    _at_ms,
    _window_bounds,
    aligned,
)


def expand_topics(log: LogLike, names: list[str], limit: int) -> tuple[list[str], int]:
    """Exact names as given, with a trailing `*` expanded to the visible topics under that prefix (at most `limit` in all).

    Returns the names to answer for, and how many were left out by the limit.
    """
    out: list[str] = []
    for name in names:
        if name.endswith("*"):
            out += [t.name for t in sorted(log.topics.values(), key=lambda t: t.name) if t.name.startswith(name[:-1]) and not t.hidden]
        else:
            out.append(name)
    unique = list(dict.fromkeys(out))
    return unique[:limit], max(0, len(unique) - limit)


def _reduce_topic(topic: Topic, rows: Rows, spec: QuerySpec, window: Range, origin: int) -> dict[str, Any]:
    """One topic's reduction over the window, plus the value it held if it never changed inside it.

    Only the part of the topic the window needs is converted: its rows inside the window, the one before it (the value held
    when it opens) and the one after it (how long the last value held). The rows are sorted, so nothing outside is read."""

    def ms(row: LogRow) -> float:  # the same conversion `aligned` does, so the bisects cut exactly where the converted rows would
        return (row[0] - origin) / US_PER_MS

    lo, hi = _window_bounds(rows, window.since_ms, window.until_ms, ms)  # `lo` rows come before the window
    hi = max(hi, lo)
    first = max(0, lo - 1)
    context = aligned(rows[first:hi + 1], origin)  # the rows a reduction may look at
    inside = context[lo - first:hi - first]
    try:
        result = reduce_rows(
            inside, Shaper(topic.type_str, spec.fields, spec.precision), spec.reduce, spec.max_samples,
            threshold=spec.threshold,
            window=Window(history=context, since_ms=window.since_ms, until_ms=window.until_ms),
        )
    except WPILibMCPError as e:  # one unsuitable topic must not hide the answers for the others
        result = {"err": str(e)}
    if not inside and lo:
        t, value = context[lo - 1 - first]  # unchanged through the whole window: say what it held rather than "no data"
        result["held"] = shape(value, topic.type_str, spec.fields, spec.precision)
        result["held_since_ms"] = round(t, 1)
    if topic.unit and "err" not in result:
        result["unit"] = topic.unit
    return result


def _one_topic(log: LogLike, name: str, loaded: dict[str, Rows], spec: QuerySpec, window: Range, origin: int) -> dict[str, Any]:
    """The reply for one requested name: its reduction, or the reason it has none."""
    if name not in log.topics:
        return {"err": NOT_IN_LOG}
    if (why := log.topics[name].unusable_for("this reduction")) is not None:
        return {"err": why}
    return _reduce_topic(log.topics[name], loaded[name], spec, window, origin)


@serialized
def query(log: LogLike, names: list[str], spec: QuerySpec, window: Range, collapse_constants: bool = True) -> dict[str, Any]:
    """The reduction `spec.reduce` of each topic over the window: `{"data": {topic: result}, ...}`.

    With `summary`, topics that never changed are listed once under `constants` (unless `collapse_constants` is False).
    """
    origin, _ = origin_us(log, window.align)
    chosen, omitted = expand_topics(log, names, spec.max_topics)
    log.activate_schemas()
    loaded = log.rows([n for n in chosen if n in log.topics])
    data: dict[str, Any] = {name: _one_topic(log, name, loaded, spec, window, origin) for name in chosen}
    out: dict[str, Any] = {}
    if window.align != "start":
        out["t0_s"] = round((origin - log.start_us) / 1e6, 3)  # where time zero is, in seconds into the log
    if spec.reduce == "summary" and collapse_constants:
        constants = {n: v["const"] for n, v in data.items() if "const" in v and not any(v.get(flag) for flag in CAVEAT_FLAGS)}
        if constants:
            data = {n: v for n, v in data.items() if n not in constants}
            out["constants"] = constants
    if shared := hoist_shared(data):
        out["all"] = shared  # fields every topic below has, with the same value
    out["data"] = data
    if omitted:
        out["omitted_topics"] = omitted
    if any(v == {"err": NOT_IN_LOG} for v in data.values()):
        out["hint"] = FIND_NAMES_HINT
    return out


@serialized
def compare(logs: dict[str, LogLike], names: list[str], spec: QuerySpec, window: Range) -> dict[str, Any]:
    """The same question asked of several logs, side by side: `{"data": {topic: {log id: result}}}`.
    Each log is aligned on its own (`align=auto` zeroes every log at its own autonomous start); `t0_s` says where each time
    zero is. A log that cannot be aligned gets an `err`; the call fails only when no log can be answered.
    """
    per_log: dict[str, dict[str, Any]] = {}
    failed: dict[str, str] = {}
    for log_id, log in logs.items():
        try:
            per_log[log_id] = query(log, names, spec, window, collapse_constants=False)
        except WPILogError as e:
            failed[log_id] = str(e)
    if not per_log:
        raise WPILogError(" ".join(f"{log_id}: {message}" for log_id, message in failed.items()))
    topics = list(dict.fromkeys(topic for result in per_log.values() for topic in result["data"]))
    out: dict[str, Any] = {"logs": {log_id: log.path.name for log_id, log in logs.items()}}
    t0 = {log_id: result["t0_s"] for log_id, result in per_log.items() if "t0_s" in result}
    if t0:
        out["t0_s"] = t0
    cell = {log_id: {"err": message} for log_id, message in failed.items()}
    out["data"] = {topic: {log_id: _cell_or_reply(cell, per_log, topic, log_id) for log_id in logs} for topic in topics}
    omitted = max((result.get("omitted_topics", 0) for result in per_log.values()), default=0)
    if omitted:
        out["omitted_topics"] = omitted
    if any(c == {"err": NOT_IN_LOG} for cells in out["data"].values() for c in cells.values()):
        out["hint"] = FIND_NAMES_HINT
    return out


def _cell_or_reply(cell: dict[str, dict[str, Any]], per_log: dict[str, dict[str, Any]], topic: str, log_id: str) -> dict[str, Any]:
    """One log's cell for one topic: its alignment error, its own reply, or `not in this log`."""
    if log_id in cell:
        return cell[log_id]
    return per_log[log_id]["data"].get(topic, {"err": NOT_IN_LOG})


@serialized
def values_at(
    log: LogLike,
    names: list[str],
    at_ms: float | None,
    fields: list[str] | None,
    align: str,
    precision: int = DEFAULT_PRECISION,
) -> dict[str, Any]:
    """Each topic's value at `at_ms` (the last update at or before it; the end of the log when None), with its time."""
    origin, _ = origin_us(log, align)
    chosen = list(dict.fromkeys(names))
    log.activate_schemas()
    loaded = log.rows([n for n in chosen if n in log.topics])
    out: dict[str, Any] = {}
    for name in chosen:
        if name not in log.topics:
            # the first miss says how to find names; the rest are short (a flat reply has no room for a reply-level hint)
            out[name] = {"err": NOT_IN_LOG if any("err" in v for v in out.values()) else f"{NOT_IN_LOG} ({FIND_NAMES_HINT})"}
            continue
        topic = log.topics[name]
        if (why := topic.unusable_for("a value lookup")) is not None:
            out[name] = {"err": why}
            continue
        rows = loaded[name]
        index = len(rows) if at_ms is None else bisect_right(rows, origin + at_ms * US_PER_MS, key=_TIME)
        if index == 0:
            out[name] = {"err": "no value yet at that time" if rows else "no updates in this log"}
            continue
        t, raw = rows[index - 1]
        entry: dict[str, Any] = {**type_field(topic.type_str), "v": shape(raw, topic.type_str, fields, precision)}
        entry["t_ms"] = round(_at_ms(t, origin), 1)
        if topic.unit:
            entry["unit"] = topic.unit
        out[name] = entry
    return out

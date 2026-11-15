"""Turning parsed JFR events into small answers: ranked tables, GC and allocation figures, what happened near a moment,
before/after. Pure functions over the dicts `jdk.events` returns (tested without a JDK). Durations are ISO-8601 strings
(`PT0.0071S`); times are ms from the start of the recording.
"""

import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal

from .errors import JfrError

_DURATION_RE = re.compile(r"^PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$")
TOP_FRAME = "frame"  # `by` value: group by the innermost stack frame
GROUP_BY_FRAME = (None, TOP_FRAME)
Sort = Literal["total", "max", "count"]  # how `hotspots` ranks its groups
STACK = "stack"  # `by` value: group by the call path (the innermost frames, or the callers of `match`)
CUMULATIVE = "cumulative"  # `by` value: every frame of the stack counts once per event (time including callees)
PATH_FRAMES, MATCH_SEARCH_FRAMES, CUMULATIVE_FRAMES = 5, 20, 16
PATH_SEPARATOR = " <- "


def stack_depth(by: str | None, match: str | None = None) -> int:
    """How many stack frames `jfr print` must include to group by `by`: none, the innermost, or enough for a path or every frame."""
    if by in GROUP_BY_FRAME:
        return 1
    if by == STACK:
        return MATCH_SEARCH_FRAMES if match else PATH_FRAMES
    return CUMULATIVE_FRAMES if by == CUMULATIVE else 0


def duration_ms(value: Any) -> float:
    """An ISO-8601 duration string (or number of nanoseconds already) as milliseconds; 0 when absent."""
    if isinstance(value, (int, float)):
        return float(value) / 1e6
    match = _DURATION_RE.match(value) if isinstance(value, str) else None
    if not match:
        return 0.0
    hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return (hours * 3600 + minutes * 60 + seconds) * 1000.0


def offset_ms(event: dict[str, Any], start: datetime | None) -> float | None:
    """When the event started, in milliseconds after the recording started (None if either time is unknown)."""
    stamp = event.get("startTime")
    if start is None or not isinstance(stamp, str):
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (when - start.replace(tzinfo=UTC)).total_seconds() * 1000.0


def _name(value: Any) -> str:
    """A readable label for a JSON value: classes and threads have a `name`, methods are `Type.method`."""
    if isinstance(value, dict):
        method = value.get("name") if isinstance(value.get("type"), dict) else None
        if method:
            return f"{java_class(_name(value['type']))}.{method}"
        for key in ("name", "javaName", "osName"):
            if value.get(key) is not None:
                return str(value[key])
        return str(value)
    return str(value)


_PRIMITIVES = {"B": "byte", "C": "char", "D": "double", "F": "float", "I": "int", "J": "long", "S": "short", "Z": "boolean"}


def java_class(name: str) -> str:
    """A JVM-internal class name as Java writes it: `[B` -> `byte[]`, `[Ljava/lang/String;` -> `java.lang.String[]`, `a/b/C` -> `a.b.C`."""
    dims = len(name) - len(name.lstrip("["))
    base = name[dims:]
    if dims:
        base = base[1:-1] if base.startswith("L") and base.endswith(";") else _PRIMITIVES.get(base, base)
    return base.replace("/", ".") + "[]" * dims


def frame_label(frame: dict[str, Any]) -> str:
    return _name(frame.get("method", {}))


def stack_labels(event: dict[str, Any], depth: int) -> list[str]:
    frames = (event.get("stackTrace") or {}).get("frames") or []
    return [frame_label(f) for f in frames[:depth]]


def key_of(event: dict[str, Any], by: str | None) -> str:
    """The group an event belongs to: its innermost frame (`by` omitted or 'frame') or the named field's label."""
    if by in GROUP_BY_FRAME:
        frames = stack_labels(event, 1)
        if not frames:
            fields = ", ".join(k for k in event if k not in ("type", "stackTrace"))
            raise JfrError(f"{event['type']} has no stack trace to group by. Pass `by` as one of its fields: {fields}.")
        return frames[0]
    if by not in event:
        raise JfrError(f"{event['type']} has no field {by!r}. Its fields: " + ", ".join(k for k in event if k != "type"))
    return _name(event[by])


def keys_of(event: dict[str, Any], by: str | None, match: str | None = None) -> list[str]:
    """The groups an event counts in (usually one). `match` keeps only events with a frame or field containing it:
    for by=stack the path then starts at the first matching frame, so it shows the callers of that method."""
    if by == CUMULATIVE:
        labels = list(dict.fromkeys(stack_labels(event, CUMULATIVE_FRAMES)))
        return labels if not match or any(match in label for label in labels) else []
    if by == STACK:
        labels = stack_labels(event, MATCH_SEARCH_FRAMES if match else PATH_FRAMES)
        if not labels:
            return []  # a sample can lack a stack (truncated or none captured); aggregate() raises if NO event has one
        if match:
            start = next((i for i, label in enumerate(labels) if match in label), None)
            return [] if start is None else [PATH_SEPARATOR.join(labels[start: start + PATH_FRAMES])]
        return [PATH_SEPARATOR.join(labels)]
    key = key_of(event, by)
    return [key] if not match or match in key else []


def _avg_us(total_ms: float, n: int) -> float:
    return round(total_ms / n * 1000, 1) if total_ms else 0


def _row(key: str, n: int, total: float, longest: float, at: float | None) -> dict[str, Any]:
    row: dict[str, Any] = {"key": key, "n": n}
    if total:
        row.update(total_ms=round(total, 2), avg_us=_avg_us(total, n), max_us=round(longest * 1000, 1))
        if at is not None:
            row["max_at_ms"] = round(at)
    return row


def aggregate(events: list[dict[str, Any]], by: str | None, start: datetime | None, match: str | None = None) -> dict[str, dict[str, Any]]:
    """key -> {n, total, max, at} over the events (duration absent counts as zero)."""
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"n": 0, "total": 0.0, "max": 0.0, "at": None})
    if by == STACK and events and not any(stack_labels(e, 1) for e in events):
        raise JfrError(f"{events[0]['type']} has no stack trace to group by. Pass `by` as one of its fields.")
    for event in events:
        ms = duration_ms(event.get("duration"))
        for key in keys_of(event, by, match):
            g = groups[key]
            g["n"] += 1
            g["total"] += ms
            if ms >= g["max"]:
                g["max"], g["at"] = ms, offset_ms(event, start)
    return groups


def hotspots(events: list[dict[str, Any]], by: str | None, start: datetime | None, top: int, sort: Sort, match: str | None = None) -> dict[str, Any]:
    groups = aggregate(events, by, start, match)
    order = {"total": lambda g: (g["total"], g["n"]), "max": lambda g: g["max"], "count": lambda g: g["n"]}[sort]
    ranked = sorted(groups.items(), key=lambda kv: order(kv[1]), reverse=True)
    out: dict[str, Any] = {
        "events": len(events), "keys": len(groups),
        "total_ms": round(sum(duration_ms(e.get("duration")) for e in events if keys_of(e, by, match)), 2),  # each event once, even by=cumulative
        "rows": [_row(key, g["n"], g["total"], g["max"], g["at"]) for key, g in ranked[:top]],
    }
    if len(ranked) > top:
        out["omitted_keys"] = len(ranked) - top
    return out


def compare(before: list[dict[str, Any]], after: list[dict[str, Any]], by: str | None, top: int) -> dict[str, Any]:
    """Per-key before/after averages and totals, and the net change summed over EVERY key (time can move between labels)."""
    old, new = aggregate(before, by, None), aggregate(after, by, None)
    rows = []
    for key in old.keys() | new.keys():
        a, b = old.get(key), new.get(key)
        delta = (b["total"] if b else 0.0) - (a["total"] if a else 0.0)
        row: dict[str, Any] = {"key": key, "delta_ms": round(delta, 2)}
        for label, g in (("before", a), ("after", b)):
            if g:
                row[label] = {"n": g["n"], "avg_us": _avg_us(g["total"], g["n"]), "total_ms": round(g["total"], 2)}
        rows.append(row)
    rows.sort(key=lambda r: abs(r["delta_ms"]), reverse=True)
    total_before = sum(g["total"] for g in old.values())
    total_after = sum(g["total"] for g in new.values())
    out: dict[str, Any] = {
        "total_before_ms": round(total_before, 2), "total_after_ms": round(total_after, 2),
        "net_ms": round(total_after - total_before, 2),
        "net_pct": round((total_after - total_before) / total_before * 100, 1) if total_before else None,
        "rows": rows[:top],
    }
    if len(rows) > top:
        out["omitted_keys"] = len(rows) - top
    if only := [r["key"] for r in rows if "before" not in r or "after" not in r][:5]:
        out["in_only_one_recording"] = only
    return out


def _pause_ms(event: dict[str, Any]) -> Any:
    """The event's own longest pause, or the summed one only when it is absent: a real 0 pause is a value, not a gap."""
    longest = event.get("longestPause")
    return event.get("sumOfPauses") if longest is None else longest


def gc_report(gcs: list[dict[str, Any]], heaps: list[dict[str, Any]], start: datetime | None, top: int) -> dict[str, Any]:
    pauses = sorted(((round(duration_ms(_pause_ms(e)), 2), e) for e in gcs), key=lambda pe: pe[0], reverse=True)
    rows = [{"at_ms": None if (at := offset_ms(e, start)) is None else round(at), "ms": ms, "gc": _name(e.get("name")), "cause": e.get("cause")} for ms, e in pauses]
    after = [e["heapUsed"] / 2**20 for e in sorted(heaps, key=lambda e: e.get("startTime") or "") if e.get("when") == "After GC" and "heapUsed" in e]
    out: dict[str, Any] = {"gcs": len(gcs), "pause_total_ms": round(sum(ms for ms, _ in pauses), 2),
                           "pause_max_ms": pauses[0][0] if pauses else 0, "longest_pauses": rows[:top]}
    if after:
        out["heap_after_gc_mb"] = {"first": round(after[0], 1), "last": round(after[-1], 1), "min": round(min(after), 1), "max": round(max(after), 1), "samples": len(after)}
        # a heuristic, stated as one: heap that still keeps growing after collections, over enough of them to see a trend
        out["heap_growing"] = len(after) >= 4 and after[-1] > after[0] * 1.2 and after[-1] >= max(after) * 0.95
    return out


def allocation_report(samples: list[dict[str, Any]], by: str, match: str | None, top: int, seconds: float | None) -> dict[str, Any]:
    needle = (match or "").lower()
    by_key: dict[str, float] = defaultdict(float)
    stacks: dict[tuple[str, ...], float] = defaultdict(float)
    total = 0.0
    for e in samples:
        weight = float(e.get("weight") or 0)
        total += weight
        cls = java_class(_name(e.get("objectClass")))
        if needle and needle not in cls.lower():
            continue
        by_key[cls if by == "class" else (stack_labels(e, 1) or ["?"])[0]] += weight
        if needle:
            stacks[tuple(stack_labels(e, 8))] += weight
    mb = 2**20
    out: dict[str, Any] = {"total_mb": round(total / mb, 1), "samples": len(samples)}
    if seconds:
        out["mb_per_s"] = round(total / mb / seconds, 2)
    out["rows"] = [{"key": k, "mb": round(v / mb, 2), "pct": round(v / total * 100, 1) if total else 0}
                   for k, v in sorted(by_key.items(), key=lambda kv: kv[1], reverse=True)[:top]]
    if needle:
        out["stacks"] = [{"mb": round(v / mb, 2), "frames": list(k)} for k, v in sorted(stacks.items(), key=lambda kv: kv[1], reverse=True)[:5]]
    return out


def leak_report(old: list[dict[str, Any]], top: int) -> dict[str, Any]:
    by_class: dict[str, int] = defaultdict(int)
    for e in old:
        by_class[java_class(_name((e.get("object") or {}).get("type")))] += 1
    return {"candidates": len(old), "rows": [{"key": k, "n": n} for k, n in sorted(by_class.items(), key=lambda kv: kv[1], reverse=True)[:top]]}


def _what(event: dict[str, Any]) -> str:
    """One line on a GC, compilation or deoptimization event: the collector and cause, or the method (and reason)."""
    if event["type"] == "jdk.GarbageCollection":
        return f"{_name(event.get('name'))} {event.get('cause', '')}".strip()
    detail = event.get("reason", "") if event["type"] == "jdk.Deoptimization" else ""
    return f"{_name(event.get('method'))} {detail}".strip()


NEAR_EVENTS = ("jdk.GarbageCollection", "jdk.Compilation", "jdk.Deoptimization")


def around(events: list[dict[str, Any]], start: datetime | None, at_ms: float, window_ms: float) -> list[dict[str, Any]]:
    """GC, JIT compilation and deoptimization events that overlap [at - window, at + window]."""
    found = []
    for e in events:
        begin = offset_ms(e, start)
        if begin is None:
            continue
        length = duration_ms(e.get("duration"))
        if begin + length < at_ms - window_ms or begin > at_ms + window_ms:
            continue
        found.append({"type": e["type"].removeprefix("jdk."), "at_ms": round(begin), "ms": round(length, 2), "what": _what(e)})
    return sorted(found, key=lambda f: f["at_ms"])

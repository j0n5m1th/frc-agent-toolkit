"""Pure reductions over captured samples: summary/changes/edges/durations/time_weighted/step_response and the rest.
Each takes the rows of one topic plus its `Window` (see `segments.py`) and answers in the reply shape: `n`, the shaped values, and
the caveat flags a gap or an out-of-order run introduced.
"""

import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from itertools import chain, pairwise
from typing import Any

from ..conditions import values_equal
from ..errors import WPILibMCPError
from ..values import is_numeric, shape, type_field
from .segments import (
    NO_WINDOW,
    Segment,
    Window,
    _in_range,
    _ordered_segments,
    _OrderState,
    _Verdict,
    split_segments,
)
from .types import MsRow, Reduce

# The two unit factors of the µs-based NT4 clock, declared once for every module that converts it.
US_PER_MS = 1000  # microseconds per millisecond, for µs -> ms
US_PER_S = 1_000_000  # microseconds per second, for µs -> s
MS_PER_S = 1000.0  # milliseconds per second, for ms -> s
MAX_DURATION_STATES = 12  # distinct values listed by reduce=durations, longest total time first
STEP_MIN_SAMPLES = 4  # reduce=step_response needs at least this many connected samples to see a rise and a settle
STEP_RISE_LOW, STEP_RISE_HIGH = 0.10, 0.90  # rise time runs from 10% to 90% of the way to the final value
STEP_SETTLE_BAND = 0.02  # settled = staying within 2% of the step size around the final value
STEP_TAIL_FRACTION = 0.10  # the final value is the mean of the last 10% of the samples

SAMPLING_MIN_UPDATES = 10  # fewer updates than this are too few to tell how a signal is logged
SAMPLING_MAX_UPDATES = 5000  # a longer signal is judged by its first updates, so a very long log costs no more than a short one
SAMPLING_PERIODIC_SHARE = 0.8  # periodic: at least this share of the gaps within half a median gap of the median gap

MAX_PEAKS = 5  # reduce=peaks lists at most this many highs and lows
PEAK_SEPARATION_MS = 500.0  # two peaks closer than this are one event

Run = tuple[Any, float, float]  # (value, start_ms, end_ms)
Weighted = tuple[float, float, float]  # (start_ms, end_ms, value held)
Column = list[list[tuple[float, float]]]  # per connected segment, (ms, number) rows of one value


@dataclass(frozen=True)
class Shaper:
    """Turns one topic's raw values into reply-ready values for its type, the fields asked for, and precision."""

    type_str: str
    fields: list[str] | None
    precision: int

    def __call__(self, raw: Any) -> Any:
        return shape(raw, self.type_str, self.fields, self.precision)


def reduce_rows(rows: list[MsRow], shaper: Shaper, reduce: Reduce, max_samples: int, *,
                threshold: float | None = None,
                window: Window = NO_WINDOW) -> dict[str, Any]:
    """The reduction `reduce` of one topic's rows, in the reply shape its tool advertises.

    `shaper` carries the topic's type, requested struct fields and precision (the three are always used together)."""
    if reduce == "time_weighted" and (rows or window.history):
        return _time_weighted(rows if window.history is None else window.history, shaper.type_str, shaper.precision, window)
    if not rows:
        return _annotate({"n": 0}, affected=window.affected, used=0)
    if reduce == "edges":
        return _edges(rows, shaper.type_str, threshold, max_samples, window)
    if reduce == "durations":
        return _durations(rows, shaper, window)
    return _reduce_segmented(rows, shaper, reduce, max_samples, window)


def _reduce_segmented(
    rows: list[MsRow],
    shaper: Shaper,
    reduce: Reduce,
    max_samples: int,
    window: Window,
) -> dict[str, Any]:
    """latest / summary / changes / samples / percentiles / rate / step_response: reductions that first split the rows at outages."""
    if reduce in ("summary", "changes", "percentiles", "rate", "step_response", "peaks", "buckets"):
        segments, affected, out_of_order = _ordered_segments(rows, window)
    else:
        segments, affected = split_segments(rows, replace(window, loss_windows=()))
        out_of_order = 0
    affected = affected or window.affected
    used = list(chain.from_iterable(segments))
    if not used:
        result: dict[str, Any] = {"n": len(rows), **({"used_samples": 0, "gap_affected": True} if affected else {})}
        return _mark_order(result, out_of_order, 0)
    if reduce == "latest":
        t, raw = max(reversed(used), key=lambda row: row[0])
        return _annotate({**type_field(shaper.type_str), "t_ms": round(t, 1), "v": shaper(raw)}, affected=affected, used=len(used))
    if reduce == "summary":
        body = _summary(rows, segments, shaper, steady=not affected and not out_of_order)
    elif reduce == "percentiles":
        body = _percentiles(rows, segments, shaper)
    elif reduce == "rate":
        body = _rate(rows, segments, shaper, steady=not affected and not out_of_order)
    elif reduce == "step_response":
        body = _step_response(rows, segments, shaper)
    elif reduce == "peaks":
        body = _peaks(rows, segments, shaper, max_samples)
    elif reduce == "buckets":
        body = _buckets(rows, segments, shaper, max_samples)
    else:
        body = _columnar(rows, segments, used, shaper, reduce, max_samples)
    return _annotate(body, affected=affected, used=len(used), out_of_order=out_of_order)


def _finite(values: Sequence[float]) -> tuple[list[float], int]:
    """The finite numbers of `values` and how many were left out (NaN and +/-inf have no min, mean or order to report)."""
    kept = [v for v in values if math.isfinite(v)]
    return kept, len(values) - len(kept)


def _nothing_finite(bad: int) -> dict[str, Any]:
    """The answer when NaN or infinity leaves nothing to compute from: `bad` samples were left out (0 when the values are finite
    but too far apart for the result to fit a float). One wording for every reduction, so a caller never reads it as a short log."""
    if bad:
        return {"non_finite": bad, "note": "No finite values to compute from (NaN or infinity)."}
    return {"note": "The values are so far apart that the result overflows a float."}


def sampling_kind(rows: Sequence[MsRow]) -> str | None:
    """How a signal is logged, judged from its first `SAMPLING_MAX_UPDATES` updates; None when there are too few to tell.

    `periodic`: logged every loop. `change_only`: irregular gaps and never the same value twice in a row, so it is logged when the value
    changes (a long gap is a hold, and a statistic over it counts updates, not seconds). `event`: anything else.
    """
    if len(rows) < SAMPLING_MIN_UPDATES:
        return None
    pairs = list(pairwise(rows[:SAMPLING_MAX_UPDATES]))
    gaps = [b[0] - a[0] for a, b in pairs]
    median = statistics.median(gaps)
    if median > 0 and sum(abs(g - median) <= median / 2 for g in gaps) >= SAMPLING_PERIODIC_SHARE * len(gaps):
        return "periodic"
    return "event" if any(values_equal(a[1], b[1]) for a, b in pairs) else "change_only"


def _summary(rows: list[MsRow], segments: list[Segment], shaper: Shaper, steady: bool) -> dict[str, Any]:
    """first/last plus min/max/mean (numeric) or change count (other); a never-changing topic collapses to `const`."""
    type_str, fields, precision = shaper.type_str, shaper.fields, shaper.precision
    used = list(chain.from_iterable(segments))
    nums, skipped = _finite([float(r[1]) for r in used]) if is_numeric(type_str) else ([], 0)
    if is_numeric(type_str) and not nums:  # every value NaN or infinity: say so rather than call it a constant
        return {**type_field(type_str), "n": len(rows), "first": shaper(used[0][1]), "last": shaper(used[-1][1]), **_nothing_finite(skipped)}
    if all(values_equal(r[1], used[0][1]) for r in used):
        return {**type_field(type_str), "n": len(rows), "const": shaper(used[0][1])}
    s: dict[str, Any] = {**type_field(type_str), "n": len(rows)}
    span = (used[-1][0] - used[0][0]) / MS_PER_S
    if span > 0 and steady:
        s["capture_rate_hz"] = round((len(used) - 1) / span, 1)
        if kind := sampling_kind(used):
            s["sampling"] = kind
    s["first"], s["last"] = shaper(used[0][1]), shaper(used[-1][1])
    if is_numeric(type_str):
        if skipped:
            s["non_finite"] = skipped  # NaN/inf updates, left out of min, max and mean
        s.update(_min_max_mean(nums, precision))
        return s
    changes = 0
    for segment in segments:
        for a, b in zip(segment, segment[1:], strict=False):
            if not values_equal(a[1], b[1]):
                changes += 1
    s["changes"] = changes
    if fields and type_str.startswith(("struct:", "proto:")):
        stats = _field_stats([shaper(r[1]) for r in used], precision)
        if stats:
            s["fields"] = stats
    return s


def _numeric_leaves(shaped: Any) -> list[tuple[str, float]]:
    """The (field, number) pairs of one shaped value: its numeric, finite struct fields (a number is keyed '')."""
    if not isinstance(shaped, dict):
        return []
    return [
        (key, float(leaf))
        for key, leaf in shaped.items()
        if isinstance(leaf, (int, float)) and not isinstance(leaf, bool) and math.isfinite(leaf)
    ]


def _number_columns(segments: list[Segment], shaper: Shaper, reduce: str) -> dict[str, Column]:
    """The numbers `percentiles`, `rate` and `step_response` work on: one column for a number topic (key ''), or one per number field
    of a struct/protobuf topic named in `fields`."""
    type_str = shaper.type_str
    if type_str in ("double", "float", "int"):
        numbers: dict[str, Column] = {}
        for segment in segments:
            numbers.setdefault("", []).append([(t, float(v)) for t, v in segment])
        return numbers
    if not type_str.startswith(("struct:", "proto:")):
        raise WPILibMCPError(
            f"reduce={reduce!r} needs a number topic (double, float or int) or a struct with `fields`; "
            f"{type_str!r} is not (use 'summary' or 'changes')."
        )
    if not shaper.fields:
        raise WPILibMCPError(
            f"reduce={reduce!r} on {type_str!r} needs `fields` naming number fields, e.g. fields=['vx'] "
            "(a topic listing with struct_fields=true shows them)."
        )
    columns: dict[str, Column] = {}
    for segment in segments:
        per_field: dict[str, list[tuple[float, float]]] = {}
        for t, raw in segment:
            for key, number in _numeric_leaves(shaper(raw)):
                per_field.setdefault(key, []).append((t, number))
        for key, rows in per_field.items():
            columns.setdefault(key, []).append(rows)
    if not columns:
        raise WPILibMCPError(f"reduce={reduce!r}: none of the fields {shaper.fields} is a number in {type_str!r}.")
    return columns


def _per_column(columns: dict[str, Column], shaper: Shaper, rows: list[MsRow], stats: Any) -> dict[str, Any]:
    """`stats(column)` for a number topic (merged into the result), or per field under `fields` for a struct."""
    if "" in columns:
        return {"n": len(rows), **stats(columns[""])}
    return {**type_field(shaper.type_str), "n": len(rows), "fields": {key: stats(col) for key, col in columns.items()}}


def _percentile(ordered: list[float], q: float) -> float:
    """The q-th percentile (0-100) of an ascending list, interpolating between neighbours."""
    pos = (len(ordered) - 1) * q / 100
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _percentiles(rows: list[MsRow], segments: list[Segment], shaper: Shaper) -> dict[str, Any]:
    """min / median / p95 / p99 / max of the stored values: the spread (jitter, worst case) that a mean hides."""
    digits = shaper.precision

    def stats(column: Column) -> dict[str, Any]:
        ordered, skipped = _finite([v for _, v in chain.from_iterable(column)])
        ordered.sort()
        if not ordered:
            return _nothing_finite(skipped)
        return {
            **({"non_finite": skipped} if skipped else {}),
            "min": round(ordered[0], digits),
            "p50": round(_percentile(ordered, 50), digits),
            "p95": round(_percentile(ordered, 95), digits),
            "p99": round(_percentile(ordered, 99), digits),
            "max": round(ordered[-1], digits),
        }

    return _per_column(_number_columns(segments, shaper, "percentiles"), shaper, rows, stats)


def _slope(pair: tuple[MsRow, MsRow]) -> tuple[float, float]:
    """(units per second, at ms) between two neighbouring samples of one segment."""
    (t0, v0), (t1, v1) = pair
    return ((v1 - v0) / ((t1 - t0) / MS_PER_S), t1)


def _min_max_mean(values: list[float], precision: int) -> dict[str, float]:
    """The three numbers that describe a column's spread."""
    return {"min": round(min(values), precision), "max": round(max(values), precision),
            "mean": round(statistics.fmean(values), precision)}


def _rate(rows: list[MsRow], segments: list[Segment], shaper: Shaper, steady: bool) -> dict[str, Any]:
    """How fast the value changed, in units per second, between neighbouring stored samples (never across an outage)."""
    digits = shaper.precision

    def stats(column: Column) -> dict[str, Any]:
        bad = sum(not math.isfinite(v) for segment in column for _, v in segment)  # samples, as every other reduction counts
        neighbours = chain.from_iterable(zip(segment, segment[1:], strict=False) for segment in column)
        pairs = [pair for pair in neighbours if pair[1][0] > pair[0][0]]
        # a NaN or inf sample makes its two slopes meaningless; so does a pair so far apart that the slope overflows
        slopes = [slope for pair in pairs if all(math.isfinite(v) for _, v in pair) and math.isfinite((slope := _slope(pair))[0])]
        if not slopes:
            if bad or any(all(math.isfinite(v) for _, v in pair) for pair in pairs):
                return _nothing_finite(bad)
            return {"note": "Need two samples at different times; record longer."}
        out: dict[str, Any] = {"mean_abs_per_s": round(statistics.fmean(abs(s) for s, _ in slopes), digits)}
        if bad:
            out["non_finite"] = bad  # samples left out, with the slopes next to them
        rise, fall = max(slopes), min(slopes)
        if rise[0] > 0:
            out["max_rise_per_s"], out["max_rise_at_ms"] = round(rise[0], digits), round(rise[1], 1)
        if fall[0] < 0:
            out["max_fall_per_s"], out["max_fall_at_ms"] = round(fall[0], digits), round(fall[1], 1)
        return out

    result = _per_column(_number_columns(segments, shaper, "rate"), shaper, rows, stats)
    if steady and len(rows) > 1 and rows[-1][0] > rows[0][0] and "fields" not in result and "note" not in result:
        result["capture_rate_hz"] = round((len(rows) - 1) / ((rows[-1][0] - rows[0][0]) / MS_PER_S), 1)
    return result


def _step_response(rows: list[MsRow], segments: list[Segment], shaper: Shaper) -> dict[str, Any]:
    """How the value responded to a step: overshoot, rise time and settling time, for tuning a control loop.
    The window should start at the setpoint change. The step runs from the first sample to `final` (the mean of the last 10%),
    so the numbers say how the signal moved, not whether it reached the setpoint. An outage breaks a response in two, so only
    the longest connected stretch is measured.
    """
    digits = shaper.precision

    def stats(column: Column) -> dict[str, Any]:
        samples = max(column, key=len)
        finite = [sample for sample in samples if math.isfinite(sample[1])]
        if not finite:
            return _nothing_finite(len(samples))
        if len(finite) < STEP_MIN_SAMPLES:
            return {"note": f"Need at least {STEP_MIN_SAMPLES} connected samples; record longer or at a higher rate.",
                    **({"non_finite": len(samples) - len(finite)} if len(finite) < len(samples) else {})}
        result = _measure_step(finite, digits)
        if len(finite) < len(samples):
            result["non_finite"] = len(samples) - len(finite)  # NaN/inf samples, left out
        return result

    return _per_column(_number_columns(segments, shaper, "step_response"), shaper, rows, stats)


def _measure_step(samples: list[tuple[float, float]], digits: int) -> dict[str, Any]:
    """Overshoot, rise and settling time of one connected run of (ms, number) samples."""
    times = [t for t, _ in samples]
    values = [v for _, v in samples]
    start = values[0]
    tail = values[-max(2, math.ceil(len(values) * STEP_TAIL_FRACTION)):]
    final = statistics.fmean(tail)
    step = final - start
    if step == 0 or abs(step) < 10 ** -digits:
        return {"note": "No step: the value ended where it started. Start the window at the setpoint change."}
    progress = [(v - start) / step for v in values]  # 0 = where it started, 1 = where it ended up
    if any(abs(p - 1) > STEP_SETTLE_BAND for p in progress[-len(tail):]):
        return {"settled": False, "start": round(start, digits), "last": round(values[-1], digits),
                "note": "The value is still moving at the end of the window; record longer to measure the response."}
    out: dict[str, Any] = {"start": round(start, digits), "final": round(final, digits)}
    peak = max(range(len(progress)), key=progress.__getitem__)
    out["overshoot_pct"] = round(max(0.0, progress[peak] - 1) * 100, 1)
    if out["overshoot_pct"] > 0:
        out["peak"], out["peak_at_ms"] = round(values[peak], digits), round(times[peak] - times[0], 1)
    low = next((i for i, p in enumerate(progress) if p >= STEP_RISE_LOW), None)
    high = next((i for i, p in enumerate(progress) if p >= STEP_RISE_HIGH), None)
    if low is None or high is None:
        return {"note": "The value never rose through 10% to 90% of the step, so there is no rise time to measure."}
    out["rise_ms"] = round(times[high] - times[low], 1)
    outside = [i for i, p in enumerate(progress) if abs(p - 1) > STEP_SETTLE_BAND]
    out["settle_ms"] = round(times[outside[-1] + 1] - times[0], 1) if outside else 0.0
    return out


def _finite_samples(column: Column) -> tuple[list[tuple[float, float]], int]:
    """A column's finite (ms, number) samples across its segments, and how many were left out."""
    kept = [s for segment in column for s in segment if math.isfinite(s[1])]
    return kept, sum(map(len, column)) - len(kept)


def _peaks(rows: list[MsRow], segments: list[Segment], shaper: Shaper, max_samples: int) -> dict[str, Any]:
    """Peaks (`high`: turning points above the median) and valleys (`low`: turning points below it) as [t_ms, value], the furthest from the
    median first, with the `median` they are measured from. Each is at least PEAK_SEPARATION_MS from a bigger one, so a spike is one event and not
    its neighbouring samples. A signal that runs in reverse shows up in `low`; a flat or one-sided signal leaves the other list empty.
    At most MAX_PEAKS of each (or `max_samples` when that is smaller)."""
    digits = shaper.precision
    count = min(max_samples, MAX_PEAKS)

    def pick(samples: list[tuple[float, float]], sign: int, median: float) -> list[list[float]]:
        padded = [-math.inf, *(sign * v for _, v in samples), -math.inf]
        turning = [s for i, s in enumerate(samples) if padded[i + 1] > padded[i] and padded[i + 1] > padded[i + 2]]  # strictly above both neighbours
        chosen: list[tuple[float, float]] = []
        for t, v in sorted((s for s in turning if sign * (s[1] - median) > 0), key=lambda s: -sign * s[1]):
            if all(abs(t - other) >= PEAK_SEPARATION_MS for other, _ in chosen):
                chosen.append((t, v))
                if len(chosen) == count:
                    break
        return [[round(t, 1), round(v, digits)] for t, v in chosen]

    def stats(column: Column) -> dict[str, Any]:
        samples, bad = _finite_samples(column)
        if not samples:
            return _nothing_finite(bad)
        median = statistics.median(v for _, v in samples)
        steps = [s for i, s in enumerate(samples) if i == 0 or s[1] != samples[i - 1][1]]  # a plateau is one point, not a peak at each end
        return {"median": round(median, digits), "high": pick(steps, 1, median), "low": pick(steps, -1, median)}

    return _per_column(_number_columns(segments, shaper, "peaks"), shaper, rows, stats)


def _buckets(rows: list[MsRow], segments: list[Segment], shaper: Shaper, max_samples: int) -> dict[str, Any]:
    """min, max and sample_mean (the plain mean of the logged samples, not time-weighted; use `time_weighted` for that) per equal time bucket, as parallel arrays (at most `max_samples` buckets across the data). Unlike `samples`,
    the min and max keep a spike inside its bucket, so the shape of a long run shows without reading every update."""
    digits = shaper.precision
    count = max(max_samples, 1)

    def stats(column: Column) -> dict[str, Any]:
        samples, bad = _finite_samples(column)
        if not samples:
            return _nothing_finite(bad)
        start, end = samples[0][0], samples[-1][0]
        width = max((end - start) / count, 1.0)  # ms; at least 1 so equal timestamps share a bucket
        grouped: dict[int, list[float]] = {}
        for t, v in samples:
            grouped.setdefault(min(int((t - start) // width), count - 1), []).append(v)
        order = sorted(grouped)
        return {"bucket_ms": round(width, 1), "t_ms": [round(start + i * width, 1) for i in order],
                "min": [round(min(grouped[i]), digits) for i in order], "max": [round(max(grouped[i]), digits) for i in order],
                "sample_mean": [round(statistics.fmean(grouped[i]), digits) for i in order]}

    return _per_column(_number_columns(segments, shaper, "buckets"), shaper, rows, stats)


def _just_changes(segments: list[Segment]) -> list[MsRow]:
    """Each segment's first row plus every row whose value differs from the one before it."""
    rows: list[MsRow] = []
    for segment in segments:
        for i, row in enumerate(segment):
            if i == 0 or not values_equal(row[1], segment[i - 1][1]):
                rows.append(row)
    return rows


def _evenly_spaced(rows: list[MsRow], max_samples: int) -> list[MsRow]:
    """`max_samples` of the rows, spread evenly and always keeping the first and the last."""
    if max_samples <= 1:
        return [rows[-1]]
    last = len(rows) - 1
    idx = sorted({round(i * last / (max_samples - 1)) for i in range(max_samples)})
    return [rows[i] for i in idx]


def _columnar(
    rows: list[MsRow],
    segments: list[Segment],
    used: list[MsRow],
    shaper: Shaper,
    reduce: str,
    max_samples: int,
) -> dict[str, Any]:
    """changes / samples as parallel arrays (topic and type once, no repeated keys), thinned to max_samples."""
    rows = _just_changes(segments) if reduce == "changes" else used
    total = len(rows)
    if total > max_samples:  # evenly spaced, always keeping first and last
        rows = _evenly_spaced(rows, max_samples)
    times = [round(r[0], 1) for r in rows]
    values = [shaper(r[1]) for r in rows]
    return {**type_field(shaper.type_str), "n": total, "t_ms": times, "v": values}


def _mark_discontinuity(result: dict[str, Any], affected: bool, used: int) -> dict[str, Any]:
    if affected:
        result["gap_affected"] = True
        result["used_samples"] = used
        result["gap_note"] = "Outages break continuity; transitions during them are unknown."
    return result


def _mark_order(result: dict[str, Any], out_of_order: int, used: int) -> dict[str, Any]:
    if out_of_order:
        result["order_affected"] = True
        result["out_of_order_updates"] = out_of_order
        result["used_samples"] = used
    return result


def _annotate(result: dict[str, Any], *, affected: bool, used: int, out_of_order: int | None = None) -> dict[str, Any]:
    """Apply the annotations a reduction owes its caller: the outage mark, then (when given) the order mark.
    Pass `out_of_order` only for ordering-sensitive reductions; time_weighted omits it because it reports its own
    `out_of_order_updates` count.
    """
    result = _mark_discontinuity(result, affected, used)
    if out_of_order is not None:
        result = _mark_order(result, out_of_order, used)
    return result


def _field_stats(shaped: list[Any], precision: int) -> dict[str, dict[str, float]]:
    """min/max/mean of each numeric struct field a caller asked for (first/last alone hide the range)."""
    columns: dict[str, list[float]] = {}
    for value in shaped:
        for key, number in _numeric_leaves(value):
            columns.setdefault(key, []).append(number)
    return {key: _min_max_mean(values, precision) for key, values in columns.items()}


def _edges(
    rows: list[MsRow],
    type_str: str,
    threshold: float | None,
    limit: int,
    window: Window = NO_WINDOW,
) -> dict[str, Any]:
    """Update timestamps at which the value first crossed the level, in ms since capture start.
    Values are sample-and-hold, so a crossing is dated by the update that revealed it, not interpolated (interpolating across a
    long hold would invent a ramp: a step at t=6.6 s would be reported seconds earlier).
    """
    if not is_numeric(type_str):
        raise WPILibMCPError(f"reduce='edges' needs a numeric or boolean topic; {type_str!r} is not (use 'durations' or 'changes').")
    if threshold is None:
        if type_str != "boolean":
            raise WPILibMCPError("reduce='edges' needs `threshold` (the level to cross), e.g. threshold=45.")
        threshold = 0.5
    rising: list[float] = []
    falling: list[float] = []
    non_finite = 0
    segments, affected, out_of_order = _ordered_segments(rows, window)
    for segment in segments:
        before: float | None = None  # the last finite value: a NaN or infinity sample says nothing, so a crossing is compared across it
        for t1, raw in segment:
            b = float(raw)
            if not math.isfinite(b):
                non_finite += 1
                continue
            if before is not None:
                up, down = before < threshold <= b, before >= threshold > b
                if up or down:
                    (rising if up else falling).append(round(t1, 1))
            before = b
    out: dict[str, Any] = {**type_field(type_str), "n": len(rows), "threshold": threshold}
    if non_finite:
        out["non_finite"] = non_finite  # NaN/inf samples, skipped
    out["timing"] = "update_timestamp"
    out["rising_ms"] = rising[:limit]
    out["falling_ms"] = falling[:limit]
    if len(rising) > limit or len(falling) > limit:
        out["truncated"] = {"rising_ms": len(rising), "falling_ms": len(falling)}
    used = sum(map(len, segments))
    return _annotate(out, affected=affected or window.affected, used=used, out_of_order=out_of_order)


def _run_stats(values: list[float]) -> dict[str, float]:
    """How many complete runs a value had and how long they lasted."""
    return {"runs": len(values), "mean_ms": round(statistics.fmean(values), 1),
            "min_ms": round(min(values), 1), "max_ms": round(max(values), 1)}


def _durations(rows: list[MsRow], shaper: Shaper, window: Window = NO_WINDOW) -> dict[str, Any]:
    """How long each value lasted, for runs of equal consecutive values. Only complete runs count (the first run's start and
    the last run's end are unknown), so a cycle must be seen for one full pass.
    """
    segments, affected, out_of_order = _ordered_segments(rows, window)
    runs_seen = 0
    complete: list[Run] = []
    for segment in segments:
        runs: list[Run] = []
        for t, raw in segment:
            if runs and values_equal(runs[-1][0], raw):
                runs[-1] = (raw, runs[-1][1], t)
            else:
                if runs:
                    runs[-1] = (runs[-1][0], runs[-1][1], t)
                runs.append((raw, t, t))
        runs_seen += len(runs)
        complete.extend(runs[1:-1])
    stats: dict[str, list[float]] = {}
    for raw, start, end in complete:
        stats.setdefault(json.dumps(shaper(raw), sort_keys=True).strip('"'), []).append(end - start)
    out: dict[str, Any] = {"n": len(rows), "runs_seen": runs_seen, "complete_runs": len(complete)}
    shown = sorted(stats.items(), key=lambda kv: sum(kv[1]), reverse=True)[:MAX_DURATION_STATES]  # a counter has hundreds
    out["states"] = {key: _run_stats(values) for key, values in shown}
    if len(stats) > len(shown):
        out["states_omitted"] = len(stats) - len(shown)  # the shortest-lived values; durations suit few-state topics
    if not complete:
        out["note"] = "need at least one full run (a value that starts and ends inside the window); record longer"
    used = sum(map(len, segments))
    return _annotate(out, affected=affected or window.affected, used=used, out_of_order=out_of_order)


@dataclass
class _Weighing:
    """Previous-value-hold intervals built row by row, reacting to the shared `_OrderState` verdict."""

    state: _OrderState
    skipped: int = 0
    ambiguous: int = 0
    out_of_order: int = 0
    intervals: list[Weighted] = field(default_factory=list)

    def add(self, t: float, previous: MsRow | None) -> None:
        """Account for the row at `t`; `previous` is the (t, value) before it in the same segment, if any."""
        verdict = self.state.classify(t)
        if verdict == _Verdict.BEHIND:
            self._behind_frontier(t, previous)
        elif verdict == _Verdict.ADVANCE:
            self._advance(t, previous)
        elif previous is not None:  # REARM / HELD: a stale-run row only blurs the interval still open
            self.ambiguous += 1

    def _behind_frontier(self, t: float, previous: MsRow | None) -> None:
        self.out_of_order += 1
        if previous is not None:
            if t <= previous[0]:
                self.skipped += 1
            else:
                self.ambiguous += 1
        while self.intervals and self.intervals[-1][0] >= t:
            self.intervals.pop()
        if self.intervals and self.intervals[-1][1] > t:
            start, _, number = self.intervals[-1]
            self.intervals[-1] = (start, t, number)

    def _advance(self, t: float, previous: MsRow | None) -> None:
        if previous is not None:
            start, number = previous
            if t == start:
                self.skipped += 1
            else:
                self.intervals.append((start, t, float(number)))


def _clip_intervals(intervals: Sequence[Weighted], window: Window) -> list[Weighted]:
    clipped: list[Weighted] = []
    for start, end, number in intervals:
        start = max(start, window.since_ms) if window.since_ms is not None else start
        end = min(end, window.until_ms) if window.until_ms is not None else end
        if end > start:
            clipped.append((start, end, number))
    return clipped


def _weighted_mean(intervals: list[Weighted], covered_ms: float, precision: int) -> float:
    """The time-weighted mean of the held values, weighted by the milliseconds each was held for."""
    return round(math.fsum(value * ((end - start) / covered_ms) for start, end, value in intervals), precision)


def _time_weighted(rows: list[MsRow], type_str: str, precision: int, window: Window = NO_WINDOW) -> dict[str, Any]:
    """The time-weighted mean under previous-value-hold, with how much of the window it actually covers."""
    if not is_numeric(type_str):
        raise WPILibMCPError("reduce='time_weighted' needs a numeric or boolean topic.")
    # Two row sets, on purpose: `inside` is what the reply counts, `holding` is the full history the previous-value
    # hold needs, so a value taken before `since_ms` still carries its weight across the start of the window.
    inside = [row for row in rows if _in_range(row[0], window)]
    inside_segments, affected = split_segments(inside, window)
    holding_segments, _ = split_segments(rows, window)
    weighing = _Weighing(_OrderState(window.evicted_frontier))
    for segment in holding_segments:
        previous: MsRow | None = None
        for t, value in segment:
            weighing.add(t, previous)
            previous = (t, value)
    intervals = _clip_intervals(weighing.intervals, window)
    bad = sum(not math.isfinite(float(value)) for _, value in inside)  # samples, counted as the other reductions do
    held = [interval for interval in intervals if math.isfinite(interval[2])]  # a NaN or infinity holds no value to weigh
    covered_ms = math.fsum(end - start for start, end, _ in held)
    result: dict[str, Any] = {**type_field(type_str), "n": len(inside), "method": "previous_value_hold"}
    result["covered_ms"] = round(covered_ms, 1)
    if bad:
        result["non_finite"] = bad
    if covered_ms:
        result["mean"] = _weighted_mean(held, covered_ms, precision)
    elif bad:
        result.update(_nothing_finite(bad))
    else:
        result["note"] = "Need two ordered samples in a connected segment; record longer."
    # The weighing pass keeps its own counters; the segment path reports only `out_of_order` via _mark_order.
    counts = (
        ("skipped_nonincreasing_intervals", weighing.skipped),
        ("out_of_order_updates", weighing.out_of_order),
        ("skipped_ambiguous_intervals", weighing.ambiguous),
    )
    for key, count in counts:
        if count:
            result[key] = count
    return _annotate(result, affected=affected or window.affected, used=sum(map(len, inside_segments)))


__all__ = ["MAX_DURATION_STATES", "Shaper", "reduce_rows"]

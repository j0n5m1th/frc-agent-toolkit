"""Timing between two event rules: which start/end pairs are trustworthy, and what their delays were. The NT4 server's
`capture/query.py` builds its `events` section from here; a pair is only as good as the coverage (`Continuity`) of its two events.
"""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import WPILibMCPError
from .models import EventComparison
from .segments import overlaps_outage
from .types import EventRecord, Gap


@dataclass(frozen=True)
class Continuity:
    """Outages and per-rule coverage against which event pairs are judged."""

    gaps: Sequence[Gap]
    omitted_gap_until: float | None
    coverage: Sequence[Sequence[Gap]]

    def uncertain(self, start: float, end: float) -> bool:
        return overlaps_outage(self.gaps, self.omitted_gap_until, start, end)

    def covered(self, index: int, start: float, through: float) -> bool:
        return any(
            round(begin, 1) < round(start, 1)
            and (finish is None or round(finish, 1) > round(through, 1))
            and not self.uncertain(begin, start)
            for begin, finish in self.coverage[index]
        )


def _validate_comparison(
    events: Sequence[EventRecord],
    sequence: int,
    rules: Sequence[Mapping[str, Any]],
    comparison: EventComparison,
) -> None:
    if not rules:
        raise WPILibMCPError("Event comparison needs a capture started with event_rules.")
    if comparison.start_rule >= len(rules) or comparison.end_rule >= len(rules):
        raise WPILibMCPError(f"Event rule indexes must be between 0 and {len(rules) - 1}.")
    if comparison.start_rule == comparison.end_rule:
        raise WPILibMCPError("Event comparison needs two different rules.")
    if any(rules[index].get("error") for index in (comparison.start_rule, comparison.end_rule)):
        raise WPILibMCPError("Cannot compare events while either event rule has an error; inspect events.rules.")
    if sequence > len(events):
        raise WPILibMCPError("Event history was overwritten; use a shorter capture before comparing events.")


def _flagged(event: EventRecord) -> bool:
    """An event whose own timing cannot be trusted (initial value, or marked uncertain)."""
    return bool(event.get("initial") or event.get("timing_uncertain"))


def _pair_unknown(
    ctx: Continuity,
    comparison: EventComparison,
    start: EventRecord,
    end: EventRecord | None,
    coverage_end: float,
) -> bool:
    t0 = start["t_ms"]
    if not ctx.covered(comparison.start_rule, t0, t0) or not ctx.covered(comparison.end_rule, t0, coverage_end):
        return True
    if _flagged(start):
        return True
    if end is None:
        return False
    return _flagged(end) or round(end["t_ms"], 1) == round(t0, 1) or ctx.uncertain(t0, end["t_ms"])


def compare_events(
    events: Sequence[EventRecord],
    sequence: int,
    rules: Sequence[Mapping[str, Any]],
    comparison: EventComparison,
    ctx: Continuity,
    window_start: float,
    window_end: float,
) -> dict[str, Any]:
    """Pair each start event with the first end event after it, and say which pairs the gaps make unreliable."""
    _validate_comparison(events, sequence, rules, comparison)
    selected = [event for event in events if window_start <= event["t_ms"] <= window_end]
    starts = sorted((e for e in selected if e["rule"] == comparison.start_rule), key=lambda e: (e["t_ms"], e["seq"]))
    ends = sorted((e for e in selected if e["rule"] == comparison.end_rule), key=lambda e: (e["t_ms"], e["seq"]))
    pairs: list[dict[str, float]] = []
    unknown = not_found = 0
    for start in starts:
        t0 = start["t_ms"]
        horizon = min(window_end, t0 + comparison.max_delay_ms) if comparison.max_delay_ms is not None else window_end
        end = next((e for e in ends if t0 <= e["t_ms"] <= horizon), None)
        if _pair_unknown(ctx, comparison, start, end, end["t_ms"] if end is not None else horizon):
            unknown += 1
        elif end is not None:
            t1 = end["t_ms"]
            pairs.append({"start_ms": round(t0, 1), "end_ms": round(t1, 1), "delay_ms": round(t1 - t0, 1)})
        elif ctx.uncertain(t0, horizon):
            unknown += 1
        else:
            not_found += 1
    result: dict[str, Any] = {
        "start_rule": comparison.start_rule, "end_rule": comparison.end_rule,
        "start_events": len(starts), "matched": len(pairs), "unknown": unknown,
        "no_following_event_in_window": not_found, "pairs": pairs[:comparison.max_pairs],
        "timing": "observed update times, rounded to 0.1 ms",
        "note": "The next end event may be reused; missing baselines, undecodable or out-of-order values and gaps make timing unknown. "
                "Only observed events are counted; timing does not prove causation.",
    }
    if len(pairs) > comparison.max_pairs:
        result["omitted_pairs"] = len(pairs) - comparison.max_pairs
    if pairs:
        delays = [pair["delay_ms"] for pair in pairs]
        result["delay_ms"] = {"min": min(delays), "max": max(delays), "mean": round(statistics.fmean(delays), 1)}
    return result


__all__ = ["Continuity", "compare_events"]

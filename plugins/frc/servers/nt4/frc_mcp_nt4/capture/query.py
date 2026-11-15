"""Query assembly for a capture: build the reply from a `Snapshot` copy of its state, so a slow reduction never blocks the
ntcore listener threads. Nothing here reaches into a `Capture`.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from frc_mcp_wpilib.reduce.events import Continuity, compare_events
from frc_mcp_wpilib.reduce.segments import CAVEAT_FLAGS, Window, overlaps_outage
from frc_mcp_wpilib.reduce.shared import hoist_shared
from frc_mcp_wpilib.reduce.stats import Shaper, reduce_rows
from frc_mcp_wpilib.reduce.types import EventRecord, MsRow, Reduce
from frc_mcp_wpilib.tooling.query_bases import ReduceKnobs
from frc_mcp_wpilib.values import DEFAULT_PRECISION, shape

from ..clock import update_age
from ..errors import NT4Error
from .buffer import MAX_TOPICS, PER_TOPIC_CAP, BufferView
from .rules import EventComparison, EventState, Trigger, TriggerState
from .types import (
    CaptureInfo,
    EventsSection,
    EvidenceEntry,
    Gap,
    HealthFields,
    LogFields,
    OmittedGaps,
    RuleSummary,
    TimestampBarrier,
)

GAP_ROUND_DIGITS = 1  # reported gap and event times are rounded to 0.1 ms; elapsed_s divides by frc_mcp_wpilib.reduce.stats.MS_PER_S
_DROPPED_INFO_KEYS = ("topics", "rate_hz")  # the info keys the reply rebuilds as `sampling` and its per-topic `data`


@dataclass
class Snapshot:
    """Copy of a capture's state taken under its lock, so a query can build its reply without holding it."""

    info: CaptureInfo
    buffer: BufferView
    rate_hz: float
    all_changes: bool
    log_path: Path | None
    topic_limit: int  # MAX_TOPICS at snapshot time, for the "ignored topics" note
    trigger_ms: float | None
    trigger_value: Any
    trigger_type: str
    units: dict[str, str]
    gaps: list[Gap]
    omitted: OmittedGaps | None
    log_bytes: int
    log_truncated: bool
    log_error: str | None
    event_sequence: int
    event_revision: int
    events: list[EventRecord]
    event_rules: list[RuleSummary]
    event_coverage: list[list[Gap]]
    end_ms: float
    log_dropped_nonfinite_time: int = 0

    @property
    def omitted_gap_until(self) -> float | None:
        return self.omitted.until_ms if self.omitted else None


@dataclass(frozen=True, kw_only=True)
class QuerySpec(ReduceKnobs):
    """What a caller asked for, with the time window resolved to absolute milliseconds: the reduce knobs (`ReduceKnobs`),
    `topics`, `since_ms`/`until_ms` and the trigger-event fields. Every field is passed by keyword.
    """

    topics: list[str] | None
    since_ms: float | None
    until_ms: float | None
    events_after: int | None = None
    max_events: int = 20
    event_comparison: EventComparison | None = None
    include_evidence: bool = False
    rel_trigger: bool = False  # since/until are offsets from the trigger time
    clock_synchronized: bool = False  # only read for evidence
    now_us: int | None = None  # only read for evidence

    @classmethod
    def from_tool_args(cls, *, topics: list[str] | None, reduce: Reduce, fields: list[str] | None,
                       since_ms: float | None, until_ms: float | None, max_samples: int,
                       threshold: float | None, max_topics: int, events_after: int | None,
                       max_events: int, event_comparison: EventComparison | None,
                       include_evidence: bool, rel_trigger: bool) -> "QuerySpec":
        """The one place tool parameters become a spec (shared by `nt4_capture_query` and `nt4_watch`).

        Sample and topic counts meet the buffer's own caps, and `fields` keeps the caller's order without repeats.
        """
        return cls(
            topics=topics,
            reduce=reduce,
            fields=None if fields is None else list(dict.fromkeys(fields)),
            since_ms=since_ms,
            until_ms=until_ms,
            max_samples=min(max_samples, PER_TOPIC_CAP),
            precision=DEFAULT_PRECISION,
            threshold=threshold,
            max_topics=min(max_topics, MAX_TOPICS),
            events_after=events_after,
            max_events=max_events,
            event_comparison=event_comparison,
            include_evidence=include_evidence,
            rel_trigger=rel_trigger,
        )


# the name the tool modules import; the assembly itself sits on the dataclass above
make_spec = QuerySpec.from_tool_args


# -- resolving and summarizing capture state ---------------------------------------------------------------
class TriggerHost(Protocol):
    """What `resolve_window` needs from a capture: whether it has a trigger and the trigger's state."""

    trigger: Trigger | None

    @property
    def trigger_state(self) -> TriggerState: ...


def resolve_window(cap: TriggerHost, since_ms: float | None, until_ms: float | None,
                   rel_trigger: bool) -> tuple[float | None, float | None]:
    """Absolute (since, until); with rel_trigger the given values are offsets from the trigger time."""
    if not rel_trigger:
        return since_ms, until_ms
    if cap.trigger is None:
        raise NT4Error("rel_trigger needs a capture started with a `trigger`.")
    state = cap.trigger_state
    if state.fired_ms is None:
        raise NT4Error("The trigger has not fired yet (pass `wait_s` to nt4_capture_query to wait for it).")
    if state.history_uncertain:
        raise NT4Error("The trigger time is uncertain due to gaps or unreliable updates; use absolute times.")
    return (None if since_ms is None else state.fired_ms + since_ms,
            None if until_ms is None else state.fired_ms + until_ms)


def rule_summary(state: EventState) -> RuleSummary:
    return {"topic": state.rule.topic, "op": state.rule.op, "observed": state.count,
            **({"waiting_for_post_gap_value": True} if state.after_gap else {}),
            **({"out_of_order_updates": state.out_of_order} if state.out_of_order else {}),
            **({"history_uncertain": True} if state.history_uncertain else {}),
            **({"waiting_for_ordered_value": True} if state.rearming_order else {}),
            **({"waiting": state.waiting} if state.waiting else {}),
            **({"error": state.error} if state.error else {})}


# -- building the reply ------------------------------------------------------------------------------------
def _visible_history(snap: Snapshot, name: str, reduce: Reduce) -> tuple[list[MsRow], tuple[Gap, ...], int]:
    """Rows the reduction may use, the topic's loss windows, and how many updates had unusable timestamps."""
    samples = snap.buffer.samples.get(name, [])
    timestamp_index, timestamp_frontier, unplaceable = snap.buffer.timestamp_barriers.get(name, TimestampBarrier(0, 0.0, 0))
    if reduce in ("latest", "samples"):
        return [(t, value) for t, value, reliable in samples if reliable], (), unplaceable
    barriers = snap.buffer.drop_barriers.get(name, []) + snap.buffer.topic_barriers.get(name, [])
    loss_windows = tuple(Gap(window.start, window.frontier) for window in barriers)
    # Keep delayed pre-loss observations so they can invalidate earlier intervals.
    history = [
        (t, value) for i, (t, value, reliable) in enumerate(samples)
        if reliable and (not unplaceable or (i >= timestamp_index and t > timestamp_frontier))
        and all(t < start or (i >= index and t > frontier) for index, start, frontier in barriers)
    ]
    return history, loss_windows, unplaceable


def _loss_notes(snap: Snapshot, name: str, reduce: Reduce, unplaceable: int, result: dict[str, Any]) -> None:
    """Add the notes that explain why this topic's result may be incomplete."""
    if unplaceable:
        result["timestamp_affected"] = True
        result["unplaceable_updates"] = unplaceable
        result["timestamp_note"] = "Some updates had unusable timestamps and were excluded; timing may be incomplete."
    if snap.buffer.dropped_by_topic.get(name) and name not in snap.buffer.drop_barriers:
        result["evicted_oldest"] = snap.buffer.dropped_by_topic[name]  # results cover only the newest retained samples
    if name in snap.buffer.drop_barriers:
        result["drop_affected"] = True
        result["drop_note"] = "An oversized update was dropped; transitions across it are unknown." + (
            " This value may be older than the dropped update." if reduce == "latest" else "")
    if name in snap.buffer.topic_barriers:
        result["topic_affected"] = True
        result["topic_note"] = "The topic was unpublished; transitions while it was absent are unknown." + (
            " This value may predate the unpublish." if reduce == "latest" else "")


def topic_result(snap: Snapshot, name: str, spec: QuerySpec, window_affected: bool) -> dict[str, Any]:
    """One topic's reduced result plus the notes explaining why it may be incomplete."""
    history, loss_windows, unplaceable = _visible_history(snap, name, spec.reduce)
    rows = history
    if spec.since_ms is not None:
        rows = [r for r in rows if r[0] >= spec.since_ms]
    if spec.until_ms is not None:
        rows = [r for r in rows if r[0] <= spec.until_ms]
    result = reduce_rows(
        rows, Shaper(snap.buffer.types.get(name, ""), spec.fields, spec.precision), spec.reduce, spec.max_samples,
        threshold=spec.threshold,
        window=Window(gaps=snap.gaps, omitted_gap_until=snap.omitted_gap_until, loss_windows=loss_windows,
                      affected=window_affected, history=history, since_ms=spec.since_ms, until_ms=spec.until_ms,
                      evicted_frontier=snap.buffer.evicted_frontiers.get(name)))
    if name in snap.units:
        result["unit"] = snap.units[name]
    _loss_notes(snap, name, spec.reduce, unplaceable, result)
    return result


def health_fields(snap: Snapshot, precision: int) -> HealthFields:
    """Capture-wide notes: trigger value, losses, ignored topics and connection gaps."""
    out: HealthFields = {}
    if snap.trigger_ms is not None:
        out["trigger_v"] = shape(snap.trigger_value, snap.trigger_type, None, precision)
    if snap.buffer.dropped:
        out["dropped"] = snap.buffer.dropped
    if snap.buffer.coalesced:
        out["coalesced"] = snap.buffer.coalesced
    if snap.buffer.ignored_topics:
        out["ignored_topics"] = f"{snap.topic_limit} topic limit reached; narrow the prefix"
        out["ignored_updates"] = snap.buffer.ignored_topics
    if snap.gaps:
        out["connection_gaps_ms"] = [[round(start, GAP_ROUND_DIGITS), None if end is None else round(end, GAP_ROUND_DIGITS)]
                                     for start, end in snap.gaps]
    if snap.omitted:
        out["omitted_gaps"] = snap.omitted.total
        out["gap_history_unknown_until_ms"] = round(snap.omitted.until_ms, GAP_ROUND_DIGITS)
    return out


def event_section(snap: Snapshot, events_after: int | None, max_events: int) -> EventsSection:
    events, sequence = snap.events, snap.event_sequence
    section: EventsSection
    if events_after is None:
        # Counts only, the form every plain query repeats: no echo of each rule's topic and op (the caller chose
        # them), no fixed note, and `overwritten` and `revision` only when they say something.
        counts_only = [cast(RuleSummary, {k: v for k, v in rule.items() if k not in ("topic", "op")})
                       for rule in snap.event_rules]
        section = {"observed": sequence, "rules": counts_only}
        if sequence > len(events):
            section["overwritten"] = sequence - len(events)
        if snap.event_revision:
            section["revision"] = snap.event_revision
    else:
        section = {
            "observed": sequence, "buffered": len(events),
            "overwritten": sequence - len(events), "revision": snap.event_revision, "rules": snap.event_rules,
            "note": "Observed events only; upstream updates may be lost.",
        }
        first = events[0]["seq"] if events else sequence + 1
        items: list[EventRecord] = []
        for event in events:
            if len(items) >= max_events:
                break
            if event["seq"] > events_after:
                items.append({**event, "t_ms": round(event["t_ms"], 1)})
        next_after = items[-1]["seq"] if items else events_after
        section["items"] = items
        section["next_after"] = next_after
        section["remaining"] = sequence - next_after
        section["missed"] = max(0, first - events_after - 1)
    uncertain_buffered = sum(bool(event.get("timing_uncertain")) for event in events)
    if uncertain_buffered:
        section["timing_uncertain_buffered"] = uncertain_buffered
    return section


def evidence_section(snap: Snapshot, names: list[str], synchronized: bool,
                     now_us: int | None) -> dict[str, EvidenceEntry]:
    evidence: dict[str, EvidenceEntry] = {}
    for name in names:
        retained = snap.buffer.samples.get(name, [])
        timed_rows = [row for row in retained if row[2]]
        local_us, server_us = snap.buffer.last_update_time.get(name, (0, 0))
        evidence[name] = {
            "retained_ms": [round(min(row[0] for row in timed_rows), 1),
                            round(max(row[0] for row in timed_rows), 1)] if timed_rows else None,
            "retained_samples": len(retained),
            **({"unplaceable_updates": len(retained) - len(timed_rows)} if len(retained) != len(timed_rows) else {}),
            "dropped": snap.buffer.dropped_by_topic.get(name, 0),
            "coalesced": snap.buffer.coalesced_by_topic.get(name, 0),
            "last_update": update_age(local_us, server_us, now_us, synchronized),
        }
    return evidence


def _check_events_after(snap: Snapshot, events_after: int | None) -> None:
    if events_after is None:
        return
    if not snap.event_rules:
        raise NT4Error("This capture has no event rules; start one with `event_rules`.")
    if events_after < 0 or events_after > snap.event_sequence:
        raise NT4Error(f"events_after must be between 0 and {snap.event_sequence}.")


def _window_bounds(snap: Snapshot, spec: QuerySpec) -> tuple[float, float, bool]:
    """The queried time range clamped to the capture, and whether an outage overlaps it."""
    start = max(0.0, spec.since_ms if spec.since_ms is not None else 0.0)
    end = min(snap.end_ms, spec.until_ms if spec.until_ms is not None else snap.end_ms)
    affected = start <= end and overlaps_outage(snap.gaps, snap.omitted_gap_until, start, end)
    return start, end, affected


def _log_fields(snap: Snapshot) -> LogFields:
    if not snap.log_path:
        return {}
    out: LogFields = {"log_bytes": snap.log_bytes}  # the path comes back from capture_start and capture_stop
    if snap.log_truncated:
        out["log_truncated"] = True
    if snap.log_error:
        out["log_error"] = snap.log_error
    if snap.log_dropped_nonfinite_time:
        out["dropped_nonfinite_time"] = snap.log_dropped_nonfinite_time  # updates left out of the .wpilog: no clock to time them by
    return out


def _unaffected_constants(out: dict[str, Any]) -> dict[str, Any]:
    """Topics that never changed (and whose result carries no caveat) need only their value."""
    return {n: v["const"] for n, v in out.items()
            if "const" in v and not any(v.get(flag) for flag in CAVEAT_FLAGS)}


def _sampling_fields(snap: Snapshot) -> dict[str, Any]:
    return {
        "requested_hz": snap.rate_hz,
        "mode": "all_changes" if snap.all_changes else "sampled",
    }


def _comparison_section(snap: Snapshot, comparison: EventComparison, window_start: float,
                        window_end: float) -> dict[str, Any]:
    continuity = Continuity(snap.gaps, snap.omitted_gap_until, snap.event_coverage)
    return compare_events(snap.events, snap.event_sequence, snap.event_rules, comparison, continuity, window_start, window_end)


def build_reply(snap: Snapshot, spec: QuerySpec) -> dict[str, Any]:
    _check_events_after(snap, spec.events_after)
    window_start, window_end, window_affected = _window_bounds(snap, spec)
    names = spec.topics if spec.topics is not None else sorted(snap.buffer.samples)
    omitted = max(0, len(names) - spec.max_topics)  # a wide (prefix) capture must not flood the reply
    shown = names[:spec.max_topics]
    out = {name: topic_result(snap, name, spec, window_affected) for name in shown}
    # a copy without the two keys this reply re-derives below, so the Snapshot's own info dict is never modified
    res = {key: value for key, value in snap.info.items() if key not in _DROPPED_INFO_KEYS}
    res["sampling"] = _sampling_fields(snap)
    res.update(health_fields(snap, spec.precision))
    if snap.event_rules:
        res["events"] = event_section(snap, spec.events_after, spec.max_events)
    if spec.event_comparison is not None:
        res["event_comparison"] = _comparison_section(snap, spec.event_comparison, window_start, window_end)
    if spec.include_evidence:
        res["evidence"] = evidence_section(snap, shown, spec.clock_synchronized, spec.now_us)
    res.update(_log_fields(snap))
    if spec.reduce == "summary":
        constants = _unaffected_constants(out)  # far smaller than a full summary each
        if constants:
            out = {n: v for n, v in out.items() if n not in constants}
            res["constants"] = constants
    if shared := hoist_shared(out):
        res["all"] = shared  # fields every topic below has, with the same value
    res["data"] = out
    if omitted:
        res["omitted_topics"] = omitted  # re-query with topics=[...] to see specific ones
    return res

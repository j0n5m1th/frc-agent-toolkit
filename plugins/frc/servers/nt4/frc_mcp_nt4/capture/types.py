"""Small record types (and the sample size estimate) shared by the capture modules; no imports from them.

Record convention: a NamedTuple for a small immutable value used by position; a frozen dataclass for a record with methods
or keyword fields; a plain dataclass only for mutable state; a TypedDict for a section of a JSON reply.
"""

import sys
from typing import Any, NamedTuple, TypedDict

from frc_mcp_wpilib.conditions import CompareOp
from frc_mcp_wpilib.reduce.types import (
    EventRecord,  # Gap re-exported for .rules
    Gap,  # Gap re-exported for .rules
)
from frc_mcp_wpilib.values import JsonValue

from ..clock import UpdateAge

MIB = 2**20  # bytes per MiB
SAMPLE_OVERHEAD_BYTES = 128  # estimated bookkeeping per retained sample, on top of the value itself


def sample_size(value: Any) -> int:
    """Conservative estimate including sample bookkeeping and Python value storage."""
    def value_size(item: Any) -> int:
        if isinstance(item, (list, tuple)):
            return sys.getsizeof(item) + sum(value_size(part) for part in item)
        return sys.getsizeof(item)

    return SAMPLE_OVERHEAD_BYTES + value_size(value)


class OmittedGaps(NamedTuple):
    """Outages trimmed from the front of the gap list: their count and the end (or start) of the newest one, in ms."""

    total: int
    until_ms: float


class LossWindow(NamedTuple):
    """An unpublish or oversized-drop loss: buffer position `index` onward, from `start` until `frontier` (ms)."""

    position: int
    start: float
    frontier: float


class TimestampBarrier(NamedTuple):
    """Updates with unusable timestamps: first affected buffer position, newest arrival time (ms) and their count."""

    position: int
    frontier: float
    updates: int


class TriggerStatusDict(TypedDict, total=False):
    """The dict forms of `TriggerState.status()`; one of these keys is set per state."""

    error: str
    waiting: str
    state: str
    fired_ms: float
    timing_uncertain: bool


class CaptureInfo(TypedDict, total=False):
    """The capture identity, elapsed time and trigger status block of a query reply."""

    id: str
    active: bool
    elapsed_s: float
    topics: list[str]
    rate_hz: float
    trigger: str | TriggerStatusDict  # "armed" is a plain word; the other states are dicts
    trigger_out_of_order_updates: int
    trigger_history_uncertain: bool


class HealthFields(TypedDict, total=False):
    """The capture-wide notes `health_fields` merges into the top level of a query reply."""

    trigger_v: JsonValue
    dropped: int
    coalesced: int
    ignored_topics: str
    ignored_updates: int
    connection_gaps_ms: list[list[float | None]]
    omitted_gaps: int
    gap_history_unknown_until_ms: float


class LogFields(TypedDict, total=False):
    """The `.wpilog` file notes `_log_fields` merges into the top level of a query reply."""

    log_bytes: int
    log_truncated: bool
    log_error: str
    dropped_nonfinite_time: int


class RuleSummary(TypedDict, total=False):
    """One event rule's status in the `events.rules` section: what it watches and what it has seen."""

    topic: str
    op: CompareOp
    observed: int
    waiting_for_post_gap_value: bool
    out_of_order_updates: int
    history_uncertain: bool
    waiting_for_ordered_value: bool
    waiting: str
    error: str


class EventsSection(TypedDict, total=False):
    """The `events` section of a query reply."""

    observed: int
    buffered: int
    overwritten: int
    revision: int
    rules: list[RuleSummary]
    note: str
    items: list[EventRecord]
    next_after: int
    remaining: int
    missed: int
    timing_uncertain_buffered: int


__all__ = ["CaptureInfo", "EvidenceEntry", "EventsSection", "Gap", "HealthFields", "LogFields", "LossWindow",
           "OmittedGaps", "RuleSummary", "TimestampBarrier", "TriggerStatusDict", "sample_size"]


class EvidenceEntry(TypedDict, total=False):
    """Per-topic retention evidence in a query reply (`include_evidence`)."""

    retained_ms: list[float] | None
    retained_samples: int
    unplaceable_updates: int
    dropped: int
    coalesced: int
    last_update: UpdateAge

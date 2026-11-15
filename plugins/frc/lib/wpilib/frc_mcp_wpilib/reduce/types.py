"""Record types the shared reducers (`stats.py`) work with."""

from typing import Any, Literal, NamedTuple, TypedDict

Reduce = Literal["latest", "summary", "changes", "samples", "edges", "durations", "time_weighted", "percentiles", "rate", "step_response"]

MsRow = tuple[float, Any]  # (t_ms, raw value): the row the shared reducers take
Segment = list[MsRow]  # rows between two discontinuities, in the order they arrived


class UpdateAge(TypedDict, total=False):
    """The `last_update` reply: age in ms since the update; `age_ms` None plus a `reason` when it is undatable.

    The nt4 clock builds these (`clock.update_age`); a widget reply carries the same record."""

    age_ms: float | None
    reason: str


class Gap(NamedTuple):
    """The one (start, end) span record, in ms; `end` is None while the span is still open.

    Serves every two-number span on a reply: an outage (`gaps`), a topic's coverage by an event rule, a loss window."""

    start: float
    end: float | None


class _EventBase(TypedDict):
    """The fields every observed event carries."""

    seq: int
    rule: int
    t_ms: float


class EventRecord(_EventBase, total=False):
    """One observed event as kept in an event history and returned under `events.items`."""

    initial: bool
    v: Any
    value_omitted: str
    timing_uncertain: bool

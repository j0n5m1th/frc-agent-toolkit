"""Trigger and event-rule definitions, plus the per-condition state they share.

Both watch one topic for a false->true crossing, so `RuleState` holds what they track between updates and the shared
evaluate/recover skeleton. They differ only in the `reject_*` hooks, `_evaluate` and what a match does.
"""

import math
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, NamedTuple, Protocol

from pydantic import BaseModel, Field, model_validator

from frc_mcp_wpilib.conditions import (
    UNSET,
    CompareOp,
    ConditionIndeterminate,
    StructDecodePending,
    Unset,
    extract,
    holds,
    validate_compare_target,
    values_equal,
)
from frc_mcp_wpilib.errors import WPILibMCPError

# Re-exported: `capture` hands EventComparison to the tools.
from frc_mcp_wpilib.reduce.models import EventComparison  # noqa: F401
from frc_mcp_wpilib.values import JsonValue

from ..errors import NT4Error
from .capture_clock import CaptureClock
from .types import Gap, TriggerStatusDict, sample_size

MAX_EVENT_VALUE_BYTES = 4096
UNPLACEABLE_WAITING = "Update timestamp is missing, in the future, or NT4 clock is not synchronized; waiting for a reliable value."
UNPUBLISHED_WAITING = "Topic unpublished; waiting for a fresh value."


class Trigger(BaseModel):
    # Trigger: a condition that ends a capture shortly after it becomes true. No class docstring (pydantic would advertise it).

    topic: str = Field(min_length=1, description="Topic name")
    op: CompareOp = Field(description="'changed' = differs from the first value seen (no value)")
    value: Any = Field(None, description="Value to compare against")
    field: str | None = Field(None, description="Struct field")
    after_s: float = Field(2.0, ge=0, le=300, description="Seconds after start before it may fire")
    immediate: bool = Field(False, description="Also fire if already true at start")

    @model_validator(mode="after")
    def _check_compare_value(self) -> "Trigger":
        validate_compare_target(self.op, self.value)
        return self


class EventRule(BaseModel):
    # EventRule: record each observed false-to-true crossing, or each value change (described on the tool parameter).

    topic: str = Field(min_length=1, description="Topic name")
    op: CompareOp = Field(description="'changed' = every value change (no value)")
    value: Any = Field(None, description="Value to compare against")
    field: str | None = Field(None, description="Struct field")
    immediate: bool = Field(False, description="Record an initial true condition")

    @model_validator(mode="after")
    def _check_compare_value(self) -> "EventRule":
        validate_compare_target(self.op, self.value)
        return self


class RuleHost(Protocol):
    """What a rule state needs from the capture that owns it when an update is rejected or a topic goes away."""

    def invalidate_coverage(self, index: int, since: float, until: float | None = None) -> None: ...

    def rearm_point(self) -> int | float: ...

    def local_clock_now_ms(self) -> float: ...


@dataclass(frozen=True)
class Admitted:
    """An update that passed admission and may now be evaluated against its condition."""

    t_ms: float
    timestamp_us: int
    prior_us: int | None  # the previous admitted timestamp (None for the first update after a reset)

    def predates(self, clock: CaptureClock) -> bool:
        """A (non-first) update stamped before the capture began can never count as a match."""
        return self.prior_us is not None and clock.predates_origin(self.timestamp_us)


class Admission(Enum):
    """How a rule state's timestamp admission ended for one update."""

    PENDING_REARM = "pending_rearm"  # not past the rearm point yet; nothing consumed
    OUT_OF_ORDER = "out_of_order"  # the rearm point was spent, but the timestamp went backwards
    ADMITTED = "admitted"


class Evaluation(NamedTuple):
    """The outcome of evaluating one admitted update."""

    matched: bool  # crossed to true (a 'changed' rule: the value changed)
    current: JsonValue  # the extracted value
    previous: Unset | JsonValue  # the state's previous condition/value before this update (UNSET when unknown)


@dataclass
class RuleState(ABC):
    rule: Trigger | EventRule
    key: str | int  # identifies the handler in Capture._handler_types
    previous: Unset | JsonValue = UNSET  # the condition (or, for 'changed' rules, the value) at the previous update
    error: str | None = None
    after_gap: bool = False
    waiting: str | None = None
    last_timestamp_us: int | None = None
    out_of_order: int = 0
    history_uncertain: bool = False
    rearming_order: bool = False
    rearm_after_us: int | float | None = None

    @property
    def finished(self) -> bool:
        """True once the state needs no more updates (a trigger that has fired)."""
        return False

    def lose_history(self) -> None:
        """Forget the previous condition so the next valid update cannot be mistaken for a crossing."""
        self.previous = UNSET

    def mark_gap_started(self) -> None:
        self.lose_history()
        self.after_gap = True
        self.last_timestamp_us = None
        self.rearming_order = False

    def recover(self) -> None:
        """A valid update was evaluated: nothing is pending any more."""
        self.error = None
        self.after_gap = False
        self.waiting = None
        self.rearming_order = False

    def fail(self, exc: Exception, *, indeterminate: bool) -> None:
        """The value could not be evaluated: surface why, and distrust the history until a later value recovers."""
        self.lose_history()
        self.history_uncertain = True  # a later valid value recovers, but the gap is unknown
        if indeterminate:
            self.waiting = str(exc)
        else:
            self.error = str(exc)

    def crossed(self, previous: Any, now_true: bool) -> bool:
        """A false->true crossing; from an unknown start only when `immediate` and nothing is rearming."""
        if not now_true:
            return False
        if previous is False:
            return True
        return (previous is UNSET and self.rule.immediate
                and not (self.after_gap or self.waiting or self.rearming_order))

    def admit_timestamp(self, timestamp_us: int) -> Admission:
        """Timestamp admission for an update that cleared the clock and gap checks.

        It must be past any pending rearm point (spent once cleared) and keep the sequence non-decreasing (strictly
        increasing while rearming). Only an admitted update becomes `last_timestamp_us`.
        """
        if self.rearm_after_us is not None and timestamp_us <= self.rearm_after_us:
            return Admission.PENDING_REARM
        self.rearm_after_us = None
        prior_us = self.last_timestamp_us
        if prior_us is not None and (timestamp_us < prior_us or (self.rearming_order and timestamp_us == prior_us)):
            return Admission.OUT_OF_ORDER
        self.last_timestamp_us = timestamp_us
        return Admission.ADMITTED

    def evaluate(self, admitted: Admitted, type_str: str, raw: Any, clock: CaptureClock) -> Evaluation | None:
        """Evaluate an admitted update and advance the state; None when it could not be evaluated (failure recorded)."""
        try:
            outcome = self._evaluate(admitted, type_str, raw, clock)
        except (ConditionIndeterminate, StructDecodePending) as e:
            self._evaluation_failed(admitted, e, indeterminate=True)
            return None
        except WPILibMCPError as e:  # from the shared condition code (a bad field, an uncomparable value)
            self._evaluation_failed(admitted, e, indeterminate=False)  # surfaced in queries; the capture keeps recording
            return None
        self.recover()  # a later valid value recovers a transient error
        return outcome

    @abstractmethod
    def _evaluate(self, admitted: Admitted, type_str: str, raw: Any, clock: CaptureClock) -> Evaluation:
        raise NotImplementedError

    def _evaluation_failed(self, admitted: Admitted, exc: Exception, *, indeterminate: bool) -> None:
        self.fail(exc, indeterminate=indeterminate)

    # Hooks called by Capture._admit and the unpublish handlers; subclasses say what each means for them.
    @abstractmethod
    def reject_unplaceable(self, host: RuleHost) -> None:
        raise NotImplementedError

    @abstractmethod
    def reject_before_gap(self, host: RuleHost, before_gap: tuple[float, float]) -> None:
        raise NotImplementedError

    @abstractmethod
    def reject_out_of_order(self, host: RuleHost, t_ms: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_unpublish(self, host: RuleHost) -> None:
        raise NotImplementedError


@dataclass
class EventState(RuleState):
    count: int = 0
    coverage_ms: deque[Gap] = field(default_factory=deque)  # intervals observed

    @property
    def index(self) -> int:
        return int(self.key)  # event rules are keyed by their position

    def begin_coverage(self, start: float) -> None:
        self.coverage_ms.append(Gap(start, None))

    def end_coverage(self, end: float) -> None:
        if self.coverage_ms and self.coverage_ms[-1].end is None:
            start = self.coverage_ms[-1].start
            self.coverage_ms[-1] = Gap(start, max(start, end))

    def invalidate_coverage(self, since: float, until: float | None = None) -> None:
        """Cut every interval that overlaps [since, until) back to `since`, and distrust the history."""
        for i, interval in enumerate(self.coverage_ms):
            if (until is None or interval.start < until) and (interval.end is None or interval.end > since):
                self.coverage_ms[i] = Gap(interval.start, max(interval.start, since))
        self.history_uncertain = True

    def _evaluate(self, admitted: Admitted, type_str: str, raw: Any, clock: CaptureClock) -> Evaluation:
        rule = self.rule
        previous = self.previous
        current = extract(type_str, raw, rule.field)
        if sample_size(current) > MAX_EVENT_VALUE_BYTES:
            raise NT4Error("Event comparison value is too large; use a scalar field.")
        matched = self._matches(current) and not admitted.predates(clock)
        return Evaluation(matched, current, previous)

    def _matches(self, current: Any) -> bool:
        """Does `current` match the rule? Also advances the rule's previous value/condition."""
        rule = self.rule
        previous = self.previous
        if rule.op == "changed":
            self.previous = current
            return previous is not UNSET and not values_equal(current, previous)
        now_true = holds(rule.op, current, rule.value, UNSET)
        self.previous = now_true
        return self.crossed(previous, now_true)

    def _evaluation_failed(self, admitted: Admitted, exc: Exception, *, indeterminate: bool) -> None:
        self.end_coverage(admitted.t_ms)
        super()._evaluation_failed(admitted, exc, indeterminate=indeterminate)

    def reject_unplaceable(self, host: RuleHost) -> None:
        host.invalidate_coverage(self.index, 0)
        self.lose_history()
        self.waiting = UNPLACEABLE_WAITING
        self.rearm_after_us = host.rearm_point()

    def reject_before_gap(self, host: RuleHost, before_gap: tuple[float, float]) -> None:
        self.out_of_order += 1
        host.invalidate_coverage(self.index, *before_gap)

    def reject_out_of_order(self, host: RuleHost, t_ms: float) -> None:
        self.out_of_order += 1
        host.invalidate_coverage(self.index, t_ms)
        self.lose_history()
        self.rearming_order = True

    def on_unpublish(self, host: RuleHost) -> None:
        now = host.local_clock_now_ms()
        if math.isfinite(now):
            self.end_coverage(now)
        else:
            host.invalidate_coverage(self.index, 0)
        self.lose_history()
        self.history_uncertain = True
        self.rearm_after_us = host.rearm_point()
        self.waiting = UNPUBLISHED_WAITING


@dataclass
class TriggerState(RuleState):
    baseline: Unset | JsonValue = UNSET  # first value seen, for op 'changed'
    fired_ms: float | None = None  # when it fired, ms since capture start
    fired_value: JsonValue = None
    fired_type: str = ""
    sub: Any = None  # own sendAll subscription: a pulse shorter than the rate window must still fire it
    handle: int | None = None

    @property
    def finished(self) -> bool:
        return self.fired_ms is not None

    def fire(self, t_ms: float, raw: Any, type_str: str) -> None:
        self.fired_ms = t_ms
        self.fired_value, self.fired_type = raw, type_str

    def status(self) -> Literal["armed"] | TriggerStatusDict:
        """The trigger's state as reported in a capture's info; the bare 'armed' string is the only non-dict form."""
        if self.error:
            return {"error": self.error}
        if self.waiting:
            return {"waiting": self.waiting}
        if self.fired_ms is None:
            if self.rearming_order:
                return {"state": "waiting_for_ordered_value"}
            if self.after_gap:
                return {"state": "waiting_for_post_gap_value"}
            return "armed"
        status: TriggerStatusDict = {"fired_ms": round(self.fired_ms, 1)}
        if self.history_uncertain:
            status["timing_uncertain"] = True
        return status

    def lose_history(self) -> None:
        super().lose_history()
        self.baseline = UNSET

    def _evaluate(self, admitted: Admitted, type_str: str, raw: Any, clock: CaptureClock) -> Evaluation:
        trig = self.rule
        current = extract(type_str, raw, trig.field)
        if self.baseline is UNSET:
            self.baseline = current
        now_true = holds(trig.op, current, trig.value, self.baseline)
        previous = self.previous
        crossing = self.crossed(previous, now_true) and not admitted.predates(clock)
        self.previous = now_true
        return Evaluation(crossing, current, previous)

    def reject_unplaceable(self, host: RuleHost) -> None:
        self.history_uncertain = True
        if self.fired_ms is None:
            self.waiting = UNPLACEABLE_WAITING
            self.lose_history()
            self.rearm_after_us = host.rearm_point()

    def reject_before_gap(self, host: RuleHost, before_gap: tuple[float, float]) -> None:
        self.out_of_order += 1
        if self.fired_ms is None or before_gap[0] <= self.fired_ms:
            self.history_uncertain = True

    def reject_out_of_order(self, host: RuleHost, t_ms: float) -> None:
        self.out_of_order += 1
        if self.fired_ms is not None:  # already fired: only the firing time can be affected
            if t_ms <= self.fired_ms:
                self.history_uncertain = True
            return
        self.history_uncertain = True
        self.lose_history()
        self.rearming_order = True

    def on_unpublish(self, host: RuleHost) -> None:
        if self.fired_ms is None:
            self.history_uncertain = True
            self.lose_history()
            self.rearm_after_us = host.rearm_point()
            self.waiting = UNPUBLISHED_WAITING

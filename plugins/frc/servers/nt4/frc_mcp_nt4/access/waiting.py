"""Waiting for a topic condition: `wait_for` holds a temporary subscription, `wait_until` polls it."""

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import (
    field as dataclass_field,  # the `field` attribute below is a struct field name
)
from types import MappingProxyType
from typing import Any, NamedTuple

import ntcore

from frc_mcp_wpilib.conditions import (
    UNSET,
    ConditionIndeterminate,
    StructDecodePending,
    Unset,
    WaitOp,
    check_compare_target,
    extract,
    holds,
)
from frc_mcp_wpilib.reduce.models import Condition
from frc_mcp_wpilib.values import JsonValue

POLL_INTERVAL_S = 0.01


@dataclass
class _Progress:
    baseline: Unset | JsonValue = UNSET  # first comparable value seen; what op="changed" compares against
    last: Any = None  # raw value of the newest sample


def _holds(op: WaitOp, cmp: Any, target: Any, baseline: Any) -> bool:
    try:
        return holds(op, cmp, target, baseline)
    except ConditionIndeterminate:
        return False  # this sample cannot decide the condition; wait for the next one


def _evaluate(topic: ntcore.Topic, value: ntcore.Value, op: WaitOp, target: Any, field: str | None,
              progress: _Progress) -> bool | None:
    """Whether one sample satisfies the condition, or None when its struct field cannot be decoded yet (a later update
    may be usable). Records the sample as the newest one seen."""
    raw = value.value()
    progress.last = raw
    try:
        cmp = extract(topic.getTypeString(), raw, field)
    except StructDecodePending:
        return None
    if progress.baseline is UNSET:
        progress.baseline = cmp
    return _holds(op, cmp, target, progress.baseline)


def _first_match(topic: ntcore.Topic, values: list[ntcore.Value], op: WaitOp, target: Any, field: str | None,
                 progress: _Progress) -> tuple[str, Any] | None:
    """Feed new samples to the condition; returns (type, raw) of the first one that satisfies it."""
    for v in values:
        if _evaluate(topic, v, op, target, field, progress):
            return topic.getTypeString(), progress.last
    return None


class WaitResult(NamedTuple):
    """A wait's outcome: whether it `met` the condition, the topic's type string, the last raw value (None if no
    value arrived) and the seconds waited. With `also` conditions, `others` holds each of their topics' newest value."""

    met: bool
    type_str: str
    raw: Any
    waited_s: float
    others: Mapping[str, tuple[str, Any]] = MappingProxyType({})  # `also` topic -> (type, newest raw value)


async def wait_until(topic: ntcore.Topic, sub: ntcore.GenericSubscriber, op: WaitOp, target: Any, field: str | None,
                     timeout_s: float) -> WaitResult:
    """Poll `sub` until the condition holds or `timeout_s` passes. Returns (met, type, last_raw, waited_s).

    Drains the subscription queue each round so a brief pulse between polls is not missed; the value already
    cached locally counts as the first sample."""
    start = time.monotonic()
    progress = _Progress()
    first = True
    while True:
        values = [v for v in sub.readQueue() if v.isValid()]
        if first and not values and sub.get().isValid():  # value already cached locally
            values = [sub.get()]
        first = False
        match = _first_match(topic, values, op, target, field, progress)
        if match is not None:
            return WaitResult(True, match[0], match[1], time.monotonic() - start)
        if time.monotonic() - start >= timeout_s:
            return WaitResult(False, topic.getTypeString(), progress.last, time.monotonic() - start)
        await asyncio.sleep(POLL_INTERVAL_S)


@dataclass
class _Clause:
    """One condition of a wait on several topics: its subscription and the newest verdict on its topic's newest value."""

    topic: ntcore.Topic
    sub: ntcore.GenericSubscriber
    op: WaitOp
    target: Any
    field: str | None
    progress: _Progress = dataclass_field(default_factory=_Progress)
    holds_now: bool = False  # sample-and-hold: true from the update that satisfied it until one that does not


async def wait_until_all(clauses: Sequence[_Clause], timeout_s: float) -> WaitResult:
    """Poll until every clause holds at the same moment, or `timeout_s` passes.

    Each round replays new samples in timestamp order and checks all clauses after each, so a short overlap between polls is
    not missed. The first clause is the main one; the others' newest values are in `others`.
    """
    start = time.monotonic()
    first = True
    pending: list[tuple[int, int, ntcore.Value]] = []  # drained but newer than the watermark: not yet safe to replay
    while True:
        samples = pending
        pending = []
        for index, clause in enumerate(clauses):
            values = [v for v in clause.sub.readQueue() if v.isValid()]
            if first and not values and clause.sub.get().isValid():  # value already cached locally
                values = [clause.sub.get()]
            samples += [(v.time(), index, v) for v in values]
        first = False
        # The subscriptions are drained one after another, so a sample that arrived between two drains can be missing
        # from an earlier one. Drain again: everything that arrived before a sample seen above is now in hand.
        watermark = max((s[0] for s in samples), default=0)
        for index, clause in enumerate(clauses):
            samples += [(v.time(), index, v) for v in clause.sub.readQueue() if v.isValid()]
        pending = [s for s in samples if s[0] > watermark]
        samples = [s for s in samples if s[0] <= watermark]
        for _, index, value in sorted(samples, key=lambda s: s[:2]):
            clause = clauses[index]
            verdict = _evaluate(clause.topic, value, clause.op, clause.target, clause.field, clause.progress)
            if verdict is not None:
                clause.holds_now = verdict
            if all(c.holds_now for c in clauses):
                return _all_result(clauses, True, time.monotonic() - start)
        if time.monotonic() - start >= timeout_s:
            return _all_result(clauses, False, time.monotonic() - start)
        await asyncio.sleep(POLL_INTERVAL_S)


def _all_result(clauses: Sequence[_Clause], met: bool, waited_s: float) -> WaitResult:
    main = clauses[0]
    others = {c.topic.getName(): (c.topic.getTypeString(), c.progress.last) for c in clauses[1:] if c.progress.last is not None}
    return WaitResult(met, main.topic.getTypeString(), main.progress.last, waited_s, others)


async def wait_for(inst: ntcore.NetworkTableInstance, name: str, op: WaitOp, target: Any, field: str | None,
                   timeout_s: float, also: Sequence[Condition] = ()) -> WaitResult:
    """Block (async) until a condition on one topic holds. Returns (met, type, last_raw, waited_s, others).

    Uses a temporary sendAll subscription, so a brief pulse between polls is not missed. The current value is checked
    first; for op="changed" it is the baseline (with no target yet, the first value is). With `also`, every condition must
    hold at the same moment, each on its topic's newest value.

    Raises the base of `NT4Error` when `op` needs a `target` and has none, when `field` does not fit the struct, or when a
    sample cannot be compared with `target`. An undecodable sample (`StructDecodePending`) just does not decide the
    condition. A timeout returns met=False.
    """
    check_compare_target(op, target)  # fail now, not only when a sample arrives
    topic = inst.getTopic(name)
    sub = topic.genericSubscribe(ntcore.PubSubOptions(sendAll=True, pollStorage=1000))
    if not also:
        try:
            return await wait_until(topic, sub, op, target, field, timeout_s)
        finally:
            del sub  # GenericSubscriber unsubscribes when its reference is dropped
    clauses = [_Clause(topic, sub, op, target, field)]
    for extra in also:
        extra_topic = inst.getTopic(extra.topic)
        extra_sub = extra_topic.genericSubscribe(ntcore.PubSubOptions(sendAll=True, pollStorage=1000))
        clauses.append(_Clause(extra_topic, extra_sub, extra.op, extra.value, extra.field))
    del sub
    try:
        return await wait_until_all(clauses, timeout_s)
    finally:
        clauses.clear()  # dropping the last references unsubscribes them

"""One-shot value reads: `get_values` subscribes briefly, and `collect_values` polls those subscriptions for a result."""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import ntcore

from frc_mcp_wpilib.values import DEFAULT_PRECISION, shape

from ..clock import UpdateAge, update_age

POLL_INTERVAL_S = 0.02
WEAK_GRACE_S = 0.4  # a weak (time 0) value may be superseded by the server's

# The two ways one topic can be unavailable in a read result (the strings are tool-visible).
NOT_ANNOUNCED = "not announced"  # no such topic on the server
NO_VALUE_YET = "no value yet"  # the topic exists but nothing has been published to it


@dataclass(frozen=True)
class Observed:
    """A topic's current value as the local client sees it; the one place that unpacks an ntcore Value for the tools."""

    type_string: str
    value: ntcore.Value

    @property
    def plain(self) -> Any:
        """The value as plain Python data (ntcore `value()`)."""
        return self.value.value()

    def shaped(self, fields: list[str] | None = None) -> Any:
        """The tool-visible rendering of `plain` for this topic's type (struct fields optional)."""
        return shape(self.plain, self.type_string, fields, DEFAULT_PRECISION)

    @property
    def local_time_us(self) -> int:
        """Local-clock time of the last update in us; 0 for a weak (default) value."""
        return self.value.time()

    @property
    def is_weak(self) -> bool:
        """True for a weak (default) value; the rule itself lives on the module-level `is_weak`."""
        return is_weak(self.value)

    @property
    def server_time_us(self) -> int:
        """Server-clock time of the last update in us, as ntcore reports it (1 for values this client published itself)."""
        return self.value.server_time()

    @property
    def reported_server_time_us(self) -> int | None:
        """The server timestamp to report, or None when ntcore gives no real one (1 = this client's own publish)."""
        return self.server_time_us if self.server_time_us > 1 else None

    def age(self, now_us: int | None, synchronized: bool) -> UpdateAge:
        """The `last_update` reply for this value, relative to the local NT4 clock `now_us`."""
        return update_age(self.local_time_us, self.server_time_us, now_us, synchronized)


def is_weak(value: ntcore.Value) -> bool:
    """A weak (set-default) value: time 0, which never overrides a real value and is superseded by one."""
    return value.time() == 0


def group_key(name: str, base: str) -> str:
    """A widget's slot for topic `name`: its path under `base`, or "_" for the base topic itself (or a name outside it)."""
    prefix = base.rstrip("/") + "/"
    return name[len(prefix):] if name.startswith(prefix) and len(name) > len(prefix) else "_"


@dataclass(frozen=True)
class ReadMiss:
    """A topic with no value to report; `hint` is one of NOT_ANNOUNCED / NO_VALUE_YET (tool-visible)."""

    hint: str


ReadResult = dict[str, Observed | ReadMiss]


def _still_waiting(inst: ntcore.NetworkTableInstance, subs: dict[str, ntcore.GenericSubscriber], now: float,
                   grace: float, weak_grace: float,
                   cache: dict[str, ntcore.Value] | None = None) -> bool:
    """Is any topic still worth waiting for? One pass reads each subscription once and records it in `cache`."""
    seen: dict[str, ntcore.Value] = {} if cache is None else cache
    waiting = False
    for n, sub in subs.items():
        v = sub.get()
        seen[n] = v
        if not v.isValid():
            if inst.getTopic(n).exists() or now < grace:
                waiting = True
        elif is_weak(v) and now < weak_grace:
            # a "set default" (ours, or a local one not yet reconciled with the server):
            # give a real value from the server a moment to arrive instead of reporting the weak one
            waiting = True
    return waiting


async def collect_values(inst: ntcore.NetworkTableInstance, subs: dict[str, ntcore.GenericSubscriber],
                         timeout_s: float, grace_s: float) -> ReadResult:
    """Wait up to `timeout_s` seconds for each subscription to deliver; returns name -> Observed or ReadMiss."""
    start = time.monotonic()
    deadline = start + timeout_s
    grace = start + min(timeout_s, grace_s)  # a just-published topic may not be announced yet
    weak_grace = start + min(timeout_s, WEAK_GRACE_S)
    latest: dict[str, ntcore.Value] = {}
    while True:
        now = time.monotonic()  # one clock read per pass, shared by the deadline and both grace checks
        if now >= deadline:
            break
        if not _still_waiting(inst, subs, now, grace, weak_grace, latest):
            break
        await asyncio.sleep(POLL_INTERVAL_S)
    out: ReadResult = {}
    for n, sub in subs.items():
        v = latest.get(n)
        if v is None:  # no pass completed (e.g. a zero timeout): read this one now
            v = sub.get()
        if v.isValid():
            out[n] = Observed(inst.getTopic(n).getTypeString(), v)
        else:
            out[n] = ReadMiss(NO_VALUE_YET if inst.getTopic(n).exists() else NOT_ANNOUNCED)
    return out


async def get_values(inst: ntcore.NetworkTableInstance, names: list[str], timeout_s: float,
                     grace_s: float = 0.3) -> ReadResult:
    """One-shot read. Subscribes only for the duration of the call.

    Returns name -> Observed (type and value), or ReadMiss (the hint says why there is no value).
    """
    opts = ntcore.PubSubOptions(periodic=0.02)  # brief, so ask the server to be prompt
    subs = {n: inst.getTopic(n).genericSubscribe(opts) for n in names}
    try:
        return await collect_values(inst, subs, timeout_s, grace_s)
    finally:
        # No close() on GenericSubscriber: it unsubscribes when garbage collected.
        subs.clear()

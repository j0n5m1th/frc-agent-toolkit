"""The capture registry: ids, MAX_CAPTURES eviction, duration budgets and failure cleanup of background captures.

`NT4Session` keeps one and delegates its `capture_*` and `watch_once` methods. `close()` runs on disconnect; `is_busy()` tells
the idle timer whether a capture still needs the connection.
"""

import asyncio
import itertools
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import ntcore

from ..errors import NT4Error
from .query import QuerySpec
from .recorder import Capture, CaptureSpec


class CaptureRegistry:
    """Every live capture of the connection, by id, with the timer that enforces each one's duration budget."""

    def __init__(self) -> None:
        self._captures: dict[str, Capture] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}  # hard duration budget per running capture
        self._ids = itertools.count(1)

    def __bool__(self) -> bool:
        return bool(self._captures)

    def all(self) -> list[Capture]:
        return list(self._captures.values())

    def register(self, cap: Capture) -> None:
        """Store an already-started capture under its own id (the one write path into `_captures`)."""
        self._captures[cap.id] = cap

    def get(self, cid: str) -> Capture:
        """The capture with id `cid`. Raises NT4Error: when there is no capture `cid`."""
        cap = self._captures.get(cid)
        if cap is None:
            raise NT4Error(f"No capture {cid!r}. Active ids: {sorted(self._captures) or 'none'}.")
        return cap

    def start(self, inst: ntcore.NetworkTableInstance, spec: CaptureSpec, *, max_captures: int,
              new_log_path: Callable[[str], Path] | None) -> Capture:
        """Create, start and register a capture. Needs the running event loop.

        At `max_captures` the oldest stopped capture without event rules is evicted; if there is none, refuse.
        Raises NT4Error: at the limit, for a malformed trigger or event rule, or when the log cannot be created.
        """
        loop = asyncio.get_running_loop()
        if len(self._captures) >= max_captures:
            stopped = [c for c in self._captures.values() if not c.active and not c.has_event_rules]
            if not stopped:
                raise NT4Error(f"{max_captures} captures are running or retaining event data; discard one with nt4_capture_stop discard=true.")
            self._cancel_budget(stopped[0].id)
            del self._captures[stopped[0].id]
        cid = f"c{next(self._ids)}"
        cap = Capture(cid, spec, new_log_path(cid) if new_log_path is not None else None)
        try:
            cap.start(inst, loop)
        except BaseException:
            created_log = cap.log_open  # only a file this capture opened itself may be removed
            cap.stop()
            if created_log and cap.log_path is not None:  # a capture that never started must not leave an empty log counting against KEEP_LOGS
                cap.log_path.unlink(missing_ok=True)
            raise
        self.register(cap)
        self._timers[cid] = loop.call_later(spec.duration_s, cap.stop)  # hard time budget
        return cap

    def stop(self, cid: str, *, discard: bool) -> Capture:
        """Stop capture `cid`; `discard` also forgets it. Raises NT4Error: when there is no capture `cid`."""
        cap = self.get(cid)
        cap.stop()
        self._cancel_budget(cid)
        if discard:
            del self._captures[cid]
        return cap

    async def watch(self, inst: ntcore.NetworkTableInstance, spec: CaptureSpec, query: QuerySpec, *,
                    max_captures: int) -> dict[str, Any]:
        """One-shot capture: record `spec.duration_s`, stop, return the result of `query`, free the capture.

        The result has no `id` or `active` keys. The capture's hard budget is 1 s longer than the recording.
        Raises NT4Error: as `start` does, or when `query.reduce` does not fit the topic.
        """
        cap = self.start(inst, replace(spec, duration_s=spec.duration_s + 1),
                         max_captures=max_captures, new_log_path=None)
        try:
            await wait_ended(cap, spec.duration_s)
            cap.stop()
            res = cap.execute_query(query)
        finally:
            if cap.id in self._captures:  # already gone if close() tore the connection down mid-watch
                self.stop(cap.id, discard=True)  # cap.stop() is a no-op here on success (already stopped above)
        res.pop("id", None)
        res.pop("active", None)
        return res

    def is_busy(self) -> bool:
        """True while a capture is recording or retaining event data, i.e. still needs the connection."""
        return any(c.active or c.has_event_rules for c in self._captures.values())

    def close(self) -> None:
        """Stop and forget every capture, cancelling their duration timers (connection teardown)."""
        for cap in self._captures.values():
            cap.stop()
        for cid in list(self._timers):
            self._cancel_budget(cid)
        self._captures.clear()

    def _cancel_budget(self, cid: str) -> None:
        """Drop the duration timer of a capture that is stopped, discarded, evicted or torn down with the connection."""
        timer = self._timers.pop(cid, None)
        if timer is not None:
            timer.cancel()


async def wait_ended(cap: Capture, timeout_s: float) -> None:
    """Return once `cap` has stopped or `timeout_s` has passed, whichever comes first (polls every 50 ms)."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while cap.active and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)

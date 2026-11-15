"""Timestamp placement for one capture: the NT local-clock origin, the frozen stop time, the clock-sync flag, the pending
rearm point, and the conversion of local-clock microseconds to ms since capture start. `Capture` owns one and delegates to it.
"""

import math
from typing import Any

import ntcore

from frc_mcp_wpilib.reduce.stats import US_PER_MS


class CaptureClock:
    """The local-clock state of one capture plus the elapsed-ms conversions built from it."""

    def __init__(self) -> None:
        self.start_local_us: int | None = None
        self.stopped_local_us: int | None = None
        self.synchronized: bool | None = None
        self.rearm_after_us: int | float | None = None

    def start(self, now_us: int | None) -> None:
        """Take the capture's origin on the NT local clock."""
        self.start_local_us = now_us

    def stop(self, now_us: int | None) -> None:
        """Freeze the local clock at the moment the capture stopped."""
        self.stopped_local_us = now_us

    def try_elapsed_ms(self, us: int | None) -> float | None:
        """Local-clock microseconds as ms since capture start; None when either timestamp is missing (no clamping).

        The one conversion: each caller decides what a missing origin means for it (fall back, infinite, arrival time)."""
        if us is None or self.start_local_us is None:
            return None
        return (us - self.start_local_us) / US_PER_MS

    def try_run_time_ms(self, active: bool, now_us: int | None) -> float | None:
        """Ms since start from the NT local clock (frozen at `stopped_local_us` once stopped); None when that clock
        cannot answer, so the caller falls back to its monotonic clock."""
        elapsed = self.try_elapsed_ms(now_us if active else self.stopped_local_us)
        return None if elapsed is None else max(0.0, elapsed)

    def arrival_ms(self, now_us: int | None) -> float:
        """The NT local clock's now, in ms since capture start; infinite when unavailable. Unlike `try_run_time_ms` it
        never falls back to the monotonic clock and does not freeze at stop: it dates when an update *arrived*."""
        elapsed = self.try_elapsed_ms(now_us)
        return math.inf if elapsed is None else max(0.0, elapsed)

    def sample_time_ms(self, value: Any, arrival_ms: float) -> float:
        """ms since capture start for a received value, from its own timestamp (falls back to arrival time, and never
        later than it)."""
        timestamp = value.time()
        elapsed = self.try_elapsed_ms(timestamp) if timestamp > 0 else None
        return arrival_ms if elapsed is None else min(max(0.0, elapsed), arrival_ms)

    def can_place_update(self, inst: ntcore.NetworkTableInstance | None, value: Any, now_us: int | None) -> bool:
        """Can this update be placed on the capture timeline? Also tracks the NT clock-sync state as a side effect,
        so call it exactly once per update."""
        if self._track_sync(inst, now_us):
            return False
        return self.is_placeable(value, now_us)

    def _track_sync(self, inst: ntcore.NetworkTableInstance | None, now_us: int | None) -> bool:
        """Record the NT clock-sync state and, when sync has just returned, set the rearm point.
        Returns True when sync alone means the value cannot be placed."""
        if inst is None:
            return False
        if inst.getServerTimeOffset() is None:
            self.synchronized = False
            return True
        if self.synchronized is False:
            if now_us is None:
                return True
            self.rearm_after_us = now_us
        self.synchronized = True
        return False

    def is_placeable(self, value: Any, now_us: int | None) -> bool:
        """Pure check: whether `value`'s timestamp can be placed on the local clock. Reads the rearm point, never sets it."""
        if self.start_local_us is None or now_us is None:
            return False
        timestamp = value.time()
        if timestamp <= 0:
            return False
        return timestamp <= now_us and (self.rearm_after_us is None or timestamp > self.rearm_after_us)

    def predates_origin(self, timestamp_us: int) -> bool:
        """Whether a local-clock timestamp is older than the capture's origin (False until the origin is set)."""
        return self.start_local_us is not None and timestamp_us < self.start_local_us

    def rearm_point(self, now_us: int | None) -> int | float:
        """Updates stamped at or before this must not rearm a condition (infinite when the clock is unavailable)."""
        return now_us if now_us is not None else math.inf

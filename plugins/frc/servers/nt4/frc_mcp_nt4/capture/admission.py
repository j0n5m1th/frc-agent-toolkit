"""Gap-aware placement of timestamped values: one capture's list of connection outages (the newest ones, plus a count and
end time for trimmed ones) and the checks built on it. `Capture` opens and closes gaps and decides what a rejected value does.
"""

from collections import deque
from typing import Any

from frc_mcp_wpilib.reduce.segments import Window
from frc_mcp_wpilib.reduce.types import Gap

from .capture_clock import CaptureClock
from .types import OmittedGaps


class CaptureAdmission:
    """The connection-gap history of one capture and the after-gap and before-gap placement checks."""

    def __init__(self, clock: CaptureClock) -> None:
        self._clock = clock
        self.gaps: deque[Gap] = deque()
        self.omitted: OmittedGaps | None = None  # set once any outage has been trimmed from the front of `gaps`

    @property
    def has_open_gap(self) -> bool:
        return bool(self.gaps) and self.gaps[-1].end is None

    def start_gap(self, gap_start: float, max_gaps: int) -> None:
        """Start an outage at `gap_start`, trimming the oldest one when `max_gaps` are already kept."""
        if len(self.gaps) == max_gaps:
            oldest = self.gaps.popleft()
            total = self.omitted.total + 1 if self.omitted else 1
            self.omitted = OmittedGaps(total, oldest.end if oldest.end is not None else oldest.start)
        self.gaps.append(Gap(gap_start, None))

    def close_gap(self, end_ms: float) -> None:
        """End the open outage at `end_ms` (the caller has checked that one is open)."""
        self.gaps[-1] = Gap(self.gaps[-1].start, end_ms)

    def continuity(self) -> Window:
        """Continuity context built from the capture's own outages (no loss windows or time range)."""
        return Window(gaps=self.gaps, omitted_gap_until=self.omitted.until_ms if self.omitted else None)

    def after_latest_gap(self, value: Any, now_us: int | None) -> bool:
        """Only timestamped post-reconnect updates can rearm or advance event conditions."""
        if not self.gaps:
            return True
        end = self.gaps[-1].end
        timestamp = value.time()
        stamped_ms = self._clock.try_elapsed_ms(timestamp)
        now_ms = self._clock.try_elapsed_ms(now_us)
        if end is None or timestamp <= 0 or stamped_ms is None or now_ms is None:
            return False
        return end < stamped_ms <= now_ms

    def before_gap(self, value: Any, now_us: int | None) -> tuple[float, float] | None:
        """The (elapsed, start) span between `value` and the first outage that starts after it, or None when the
        value is not stamped before any outage."""
        timestamp = value.time()
        if now_us is None or timestamp <= 0 or timestamp > now_us:
            return None
        stamped_ms = self._clock.try_elapsed_ms(timestamp)
        if stamped_ms is None:
            return None
        elapsed = max(0.0, stamped_ms)
        for start, end in self.gaps:
            if elapsed < start:
                return elapsed, start
            if end is None or elapsed <= end:
                return None
        return None

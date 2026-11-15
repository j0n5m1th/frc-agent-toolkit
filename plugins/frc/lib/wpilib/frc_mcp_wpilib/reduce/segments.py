"""The window a reduction works in: its outages and loss windows, the segments they cut, and arrival order. `Window` is the
continuity context for one topic, `split_segments` cuts rows at its boundaries, `_OrderState` is the arrival-order decision the two
reduction paths share. Reductions are in `stats.py`; event-pair timing in `events.py`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

from .types import Gap, MsRow, Segment


@dataclass(frozen=True)
class Window:
    """Continuity context for one topic's reduction: outages, losses and the queried time range."""

    gaps: Sequence[Gap] = ()  # connection outages; sequences of [start_ms, end_ms | None] are normalized to Gap
    omitted_gap_until: float | None = None  # samples at or before this were dropped by an earlier outage
    loss_windows: Sequence[Gap] = ()  # unpublish/drop windows for this topic, as (start_ms, end_ms) spans
    affected: bool = False  # the queried window itself overlaps an outage
    history: list[MsRow] | None = None  # unwindowed rows, used to judge ordering beyond the window
    since_ms: float | None = None
    until_ms: float | None = None
    evicted_frontier: float | None = None  # newest timestamp evicted from the buffer

    def __post_init__(self) -> None:
        if any(type(gap) is not Gap for gap in self.gaps):  # a live deque of Gap is kept as-is (no per-sample copy)
            object.__setattr__(self, "gaps", tuple(Gap(*gap) for gap in self.gaps))


NO_WINDOW = Window()


# Every caveat flag a topic result can carry; each is set by one helper: gap_affected (`stats._mark_discontinuity`), order_affected
# (`stats._mark_order`), and timestamp_affected, drop_affected, topic_affected (`query._loss_notes`: unusable timestamps, an
# oversized update dropped, the topic unpublished).
CAVEAT_FLAGS: tuple[str, ...] = (
    "gap_affected",
    "order_affected",
    "timestamp_affected",
    "drop_affected",
    "topic_affected",
)


def overlaps_outage(gaps: Sequence[Gap], omitted_gap_until: float | None, start: float, end: float) -> bool:
    """Whether the closed interval [start, end] touches an outage, or reaches back to samples an earlier one dropped.
    Each gap is (start_ms, end_ms | None); None means still open. Touching an endpoint counts. Anything at or before
    omitted_gap_until is uncertain, since samples up to it were dropped.
    """
    return (omitted_gap_until is not None and start <= omitted_gap_until) or any(
        gap_start <= end and (gap_end is None or gap_end >= start) for gap_start, gap_end in gaps)


def inside_outage(t: float, window: Window) -> bool:
    """Whether t falls in one of the window's outages, or among the samples an earlier outage dropped.

    The point case of overlaps_outage (start == end == t).
    """
    return overlaps_outage(window.gaps, window.omitted_gap_until, t, t)


def _crosses_start(previous_t: float, t: float, intervals: Sequence[Gap]) -> bool:
    """Does an interval begin strictly between two consecutive observations?"""
    low, high = min(previous_t, t), max(previous_t, t)
    return any(low < start < high for start, _ in intervals)


def split_segments(rows: Sequence[MsRow], window: Window = NO_WINDOW) -> tuple[list[Segment], bool]:
    """Split observations at outages or loss boundaries, reporting only actual outages as gaps."""
    gaps, loss_windows = window.gaps, window.loss_windows
    segments: list[Segment] = []
    current: Segment = []
    affected = False
    for row in rows:
        t = row[0]
        inside = inside_outage(t, window)
        if inside or any(end is not None and start <= t <= end for start, end in loss_windows):
            if current:
                segments.append(current)
                current = []
            affected = affected or inside
            continue
        if current:
            gap_crossed = _crosses_start(current[-1][0], t, gaps)
            if gap_crossed or _crosses_start(current[-1][0], t, loss_windows):
                segments.append(current)
                current = []
                affected = affected or gap_crossed
        current.append(row)
    if current:
        segments.append(current)
    return segments, affected


def _in_range(t: float, window: Window) -> bool:
    return (window.since_ms is None or t >= window.since_ms) and (window.until_ms is None or t <= window.until_ms)


# The four verdicts `_OrderState.classify` hands back; both reduction paths react to these same values.
class _Verdict(Enum):
    BEHIND = auto()
    REARM = auto()
    HELD = auto()
    ADVANCE = auto()


@dataclass
class _OrderState:
    """The one frontier/rearm decision the reductions share: behind, a new baseline, still stale, or advancing.
    `_reliable_order` and `stats._Weighing` walk the same rows and must agree, so the state is mutated only here.
    """

    frontier: float | None  # newest timestamp accepted so far
    rearming: bool = False

    def classify(self, t: float) -> _Verdict:
        """Which case `t` falls into, applying the frontier/rearming update both paths expect."""
        if self.frontier is not None and t < self.frontier:
            self.rearming = True
            return _Verdict.BEHIND
        if self.rearming:
            if self.frontier is None or t > self.frontier:  # first row past the stale run: the new baseline
                self.rearming = False
                self.frontier = t
                return _Verdict.REARM
            return _Verdict.HELD  # still inside the stale run: neither a baseline nor an accepted sample
        self.frontier = t if self.frontier is None else max(self.frontier, t)
        return _Verdict.ADVANCE


def _retract(current: Segment, ordered: list[Segment], t: float) -> None:
    """A late sample at `t` makes every transition already recorded at or after `t` unreliable: drop them."""
    while current and current[-1][0] >= t:
        current.pop()
    while ordered and ordered[-1] and ordered[-1][-1][0] >= t:
        ordered[-1].pop()
        if not ordered[-1]:
            ordered.pop()


def _reliable_order(history_segments: Sequence[Segment], frontier: float | None) -> tuple[list[Segment], int]:
    """Walk history in arrival order, discarding samples behind the newest timestamp already seen (out of order)."""
    ordered: list[Segment] = []
    state = _OrderState(frontier)
    out_of_order = 0
    for segment in history_segments:
        current: Segment = []
        for row in segment:
            t = row[0]
            verdict = state.classify(t)
            if verdict == _Verdict.BEHIND:
                out_of_order += 1
                _retract(current, ordered, t)
            elif verdict == _Verdict.REARM:  # the new baseline opens a fresh run of rows
                if current:
                    ordered.append(current)
                current = [row]
            elif verdict == _Verdict.ADVANCE:
                current.append(row)
            # HELD keeps no row, matching the interval path's no-interval reaction.
        if current:
            ordered.append(current)
    return ordered, out_of_order


def _ordered_segments(rows: Sequence[MsRow], window: Window) -> tuple[list[Segment], bool, int]:
    """Keep only transitions whose ordering remains reliable in retained history."""
    segments, affected = split_segments(rows, window)
    history_segments = segments if window.history is None else split_segments(window.history, window)[0]
    ordered, out_of_order = _reliable_order(history_segments, window.evicted_frontier)
    selected = [[row for row in segment if _in_range(row[0], window)] for segment in ordered]
    return [segment for segment in selected if segment], affected, out_of_order


__all__ = [
    "CAVEAT_FLAGS",
    "NO_WINDOW",
    "Window",
    "inside_outage",
    "overlaps_outage",
    "split_segments",
]

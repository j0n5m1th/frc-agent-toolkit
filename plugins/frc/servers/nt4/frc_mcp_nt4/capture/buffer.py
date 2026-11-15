"""Bounded sample storage for a capture: per-topic buffers, eviction, coalescing and loss-window bookkeeping.
It knows nothing about ntcore or locks; `Capture` calls it under its own lock.
"""

import heapq
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from frc_mcp_wpilib.reduce.segments import Window, split_segments

from .types import MIB, LossWindow, TimestampBarrier

PER_TOPIC_CAP = 5000  # samples kept per topic; oldest dropped (counted in `dropped`)
MAX_TOPICS = 200  # a broad prefix must not create unbounded buffers
MAX_TOTAL = 100_000  # samples per capture across all topics (~ a few MB); oldest evicted first
MAX_BUFFER_BYTES = 16 * MIB  # estimated retained sample storage per capture
MAX_LOSS_WINDOWS = 32  # unpublish/drop loss windows kept per topic; the oldest two fold together beyond this

Sample = tuple[float, Any, bool]  # (t_ms, value, reliable timestamp)


@dataclass(frozen=True)
class BufferView:
    """Copy of a SampleBuffer's queryable state, so a query can read it without holding the capture's lock.

    Every field keeps the name of the `SampleBuffer` attribute it was copied from, so one key reads either object.
    """

    samples: dict[str, list[Sample]]
    types: dict[str, str]
    dropped: int
    coalesced: int
    ignored_topics: int
    dropped_by_topic: dict[str, int]
    evicted_frontiers: dict[str, float]
    drop_barriers: dict[str, list[LossWindow]]
    topic_barriers: dict[str, list[LossWindow]]
    timestamp_barriers: dict[str, TimestampBarrier]
    coalesced_by_topic: dict[str, int]
    last_update_time: dict[str, tuple[int, int]]


class SampleBuffer:
    def __init__(self, rate_hz: float, all_changes: bool) -> None:
        self.rate_hz = rate_hz
        self.all_changes = all_changes
        self.dropped = 0
        self.coalesced = 0
        self.ignored_topics = 0
        self.total = 0
        self.bytes = 0
        self.rate_window: dict[str, int] = {}  # topic -> index of the last recorded rate window
        self.unordered_windows: set[str] = set()
        self.samples: dict[str, deque[Sample]] = {}
        self.sizes: dict[str, deque[int]] = {}
        self.types: dict[str, str] = {}
        self.dropped_by_topic: dict[str, int] = {}
        self.evicted_frontiers: dict[str, float] = {}
        self.drop_barriers: dict[str, list[LossWindow]] = {}
        self.topic_barriers: dict[str, list[LossWindow]] = {}
        self.timestamp_barriers: dict[str, TimestampBarrier] = {}
        # Barrier positions are absolute (index plus `front_offset[topic]`, the samples popped so far), so evicting only bumps the
        # offset; `view` and the `*_windows` methods turn them back into buffer indices (clamped at 0).
        self.front_offset: dict[str, int] = {}
        # Global eviction order: a heap of (oldest sample's t_ms, topic order, topic), lazily deleted; changed topics wait in `_dirty`.
        self._order: dict[str, int] = {}
        self._heap: list[tuple[float, int, str]] = []
        self._dirty: set[str] = set()
        self.coalesced_by_topic: dict[str, int] = {}
        self.last_update_time: dict[str, tuple[int, int]] = {}  # topic -> (local us, server us) of the newest update

    # -- query-side copies ------------------------------------------------------
    def has_topic(self, name: str) -> bool:
        """Whether `name` has a sample buffer, which the methods below expect."""
        return name in self.samples

    def note_update_time(self, name: str, local_us: int, server_us: int) -> None:
        """Stamp the newest update of `name`: its own timestamp and the server time it carries (both in us)."""
        self.last_update_time[name] = (local_us, server_us)

    def view(self, topics: list[str] | None) -> BufferView:
        return BufferView(
            samples={n: list(rows) for n, rows in self.samples.items() if topics is None or n in topics},
            types=dict(self.types),
            dropped=self.dropped, coalesced=self.coalesced, ignored_topics=self.ignored_topics,
            dropped_by_topic=dict(self.dropped_by_topic),
            evicted_frontiers=dict(self.evicted_frontiers),
            drop_barriers={n: self._relative(n, w) for n, w in self.drop_barriers.items()},
            topic_barriers={n: self._relative(n, w) for n, w in self.topic_barriers.items()},
            timestamp_barriers={n: self._relative_timestamp(n, b) for n, b in self.timestamp_barriers.items()},
            coalesced_by_topic=dict(self.coalesced_by_topic),
            last_update_time=dict(self.last_update_time),
        )

    # -- barrier positions ------------------------------------------------------
    def _relative(self, name: str, windows: list[LossWindow]) -> list[LossWindow]:
        offset = self.front_offset.get(name, 0)
        return [LossWindow(max(0, w.position - offset), w.start, w.frontier) for w in windows]

    def _relative_timestamp(self, name: str, barrier: TimestampBarrier) -> TimestampBarrier:
        return TimestampBarrier(max(0, barrier.position - self.front_offset.get(name, 0)), barrier.frontier,
                                barrier.updates)

    def _absolute(self, name: str) -> int:
        """The absolute position of the end of `name`'s buffer, where a new barrier is recorded."""
        return len(self.samples[name]) + self.front_offset.get(name, 0)

    def drop_windows(self, name: str) -> list[LossWindow]:
        """`name`'s oversized-drop loss windows as buffer indices."""
        return self._relative(name, self.drop_barriers[name])

    def topic_windows(self, name: str) -> list[LossWindow]:
        """`name`'s unpublish loss windows as buffer indices."""
        return self._relative(name, self.topic_barriers[name])

    # -- bookkeeping ------------------------------------------------------------
    def _count_dropped(self, name: str) -> None:
        self.dropped += 1
        self.dropped_by_topic[name] = self.dropped_by_topic.get(name, 0) + 1

    def _count_coalesced(self, name: str) -> None:
        self.coalesced += 1
        self.coalesced_by_topic[name] = self.coalesced_by_topic.get(name, 0) + 1

    def _drop_size(self, name: str, last: bool) -> None:
        """Drop one sample's size entry; the two counters only ever move with the sample they belong to."""
        self.bytes -= self.sizes[name].pop() if last else self.sizes[name].popleft()
        self.total -= 1

    def evict(self, name: str) -> None:
        t_ms, _, reliable = self.samples[name].popleft()
        if reliable:
            self.evicted_frontiers[name] = max(t_ms, self.evicted_frontiers.get(name, t_ms))
        self._drop_size(name, last=False)
        self.front_offset[name] = self.front_offset.get(name, 0) + 1
        self._dirty.add(name)  # its oldest sample changed
        self._count_dropped(name)

    @staticmethod
    def add_barrier(store: dict[str, list[LossWindow]], name: str, index: int, start: float, frontier: float) -> None:
        """Record one loss window; each is kept apart so clean data between two losses survives."""
        windows = store.setdefault(name, [])
        windows.append(LossWindow(index, start, frontier))
        if len(windows) > MAX_LOSS_WINDOWS:  # bounded: fold the two oldest into one wider window
            first, second = windows[0], windows[1]
            windows[:2] = [LossWindow(max(first.position, second.position), min(first.start, second.start),
                                      max(first.frontier, second.frontier))]

    @staticmethod
    def _newest_reliable(buf: deque[Sample]) -> float:
        return max((row[0] for row in buf if row[2]), default=0.0)

    # -- recording --------------------------------------------------------------
    def topic_buffer(self, name: str, type_str: str) -> deque[Sample] | None:
        """The topic's sample buffer, created on first sight (None once MAX_TOPICS is reached); a changed type
        discards what was recorded under the old one."""
        buf = self.samples.get(name)
        if buf is None:
            if len(self.samples) >= MAX_TOPICS:
                self.ignored_topics += 1
                return None
            buf = self.samples[name] = deque()
            self._order[name] = len(self._order)
            self.sizes[name] = deque()
            self.types[name] = type_str
        elif type_str:
            if self.types[name] and self.types[name] != type_str:
                while buf:
                    self.evict(name)
            self.types[name] = type_str
        return buf

    def note_unpublish(self, name: str, now_ms: float) -> None:
        """The topic was unpublished at `now_ms` (inf when the clock is unavailable): mark the continuity break."""
        buf = self.samples[name]
        if not math.isfinite(now_ms):
            count = self.timestamp_barriers.get(name, TimestampBarrier(0, 0.0, 0)).updates
            self.timestamp_barriers[name] = TimestampBarrier(self._absolute(name), math.inf, count + 1)
        else:
            self.add_barrier(self.topic_barriers, name, self._absolute(name), now_ms, max(now_ms, self._newest_reliable(buf)))
        self.unordered_windows.add(name)

    def note_unplaceable(self, name: str, arrival_ms: float) -> None:
        """Record that `name` received an update whose timestamp cannot be placed, arriving at `arrival_ms`."""
        previous = self.timestamp_barriers.get(name, TimestampBarrier(0, 0.0, 0))
        self.timestamp_barriers[name] = TimestampBarrier(self._absolute(name), max(previous.frontier, arrival_ms),
                                                         previous.updates + 1)

    def record(self, name: str, sample: Sample, size: int, continuity: Callable[[], Window]) -> None:
        """Keep one update, or count it as dropped when it alone exceeds the byte budget."""
        if size > MAX_BUFFER_BYTES:
            self._count_drop(name, sample[0])
        else:
            self._append(name, sample, size, continuity)

    def _count_drop(self, name: str, t_ms: float) -> None:
        """An update too large to retain: count it and mark the continuity break it causes."""
        buf = self.samples[name]
        self._count_dropped(name)
        self.add_barrier(self.drop_barriers, name, self._absolute(name), t_ms, max(t_ms, self._newest_reliable(buf)))
        self.unordered_windows.add(name)

    def _should_coalesce(self, name: str, sample: Sample, same_window: bool, continuity: Callable[[], Window]) -> bool:
        """Whether this sample replaces the previous one (same rate window, no gap between them, not out of order)."""
        buf = self.samples[name]
        t_ms, raw, reliable = sample
        if not (same_window and buf and reliable and buf[-1][2] and name not in self.unordered_windows):
            return False
        segments, _ = split_segments([buf[-1][:2], (t_ms, raw)], continuity())
        return len(segments) == 1 and len(segments[0]) == 2

    def _replace_last(self, name: str) -> None:
        self.samples[name].pop()
        self._drop_size(name, last=True)
        self._count_coalesced(name)

    def _make_room(self, name: str, size: int) -> None:
        """Evict oldest samples until one more of `size` bytes fits the per-topic, total and byte caps."""
        if len(self.samples[name]) >= PER_TOPIC_CAP:
            self.evict(name)
        while self.total and (self.total >= MAX_TOTAL or self.bytes + size > MAX_BUFFER_BYTES):
            oldest = self._oldest_topic()
            if oldest is None:
                break
            self.evict(oldest)

    def reindex(self) -> None:
        """Rebuild the global eviction order from the buffers as they are now (after they were changed from outside)."""
        self._dirty.clear()
        self._heap = [(rows[0][0], self._order.setdefault(n, len(self._order)), n) for n, rows in self.samples.items() if rows]
        heapq.heapify(self._heap)

    def _oldest_topic(self) -> str | None:
        """The topic whose oldest sample has the smallest time (the first-seen topic on a tie), None if all are empty."""
        heap = self._heap
        if self._dirty:
            for n in self._dirty:
                rows = self.samples[n]
                if rows:
                    heapq.heappush(heap, (rows[0][0], self._order[n], n))
            self._dirty.clear()
        if len(heap) > 4 * len(self.samples) + 64:  # stale entries pile up only slowly; drop them all at once
            self.reindex()
            heap = self._heap
        while heap:
            t_ms, _, n = heap[0]
            rows = self.samples[n]
            if rows and rows[0][0] == t_ms:
                heapq.heappop(heap)  # evict() marks the topic dirty, which queues its next oldest sample
                return n
            heapq.heappop(heap)  # stale: that sample is gone (its topic is queued again under its new oldest)
        return None

    def _append(self, name: str, sample: Sample, size: int, continuity: Callable[[], Window]) -> None:
        buf = self.samples[name]
        t_ms = sample[0]
        win = -1 if self.all_changes else int(time.monotonic() * self.rate_hz)
        same_window = win >= 0 and self.rate_window.get(name) == win
        if not same_window:
            self.unordered_windows.discard(name)
        if win >= 0 and buf and t_ms < buf[-1][0]:
            self.unordered_windows.add(name)
        if self._should_coalesce(name, sample, same_window, continuity):
            self._replace_last(name)
        self._make_room(name, size)
        buf.append(sample)
        self.sizes[name].append(size)
        self.bytes += size
        self.total += 1
        if len(buf) == 1:
            self._dirty.add(name)  # first sample of an empty buffer is now its oldest
        self.rate_window[name] = win

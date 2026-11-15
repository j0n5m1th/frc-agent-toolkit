"""The sample buffer's global eviction order and absolute barrier positions behave exactly like the original scan.

`OldBuffer` is the implementation before the change (a `min()` over every topic per eviction and barrier lists
rebuilt on every eviction), kept as an oracle: random sequences must leave both buffers in the same state after every step.
"""

import math
import random
import time
from collections import deque

import pytest

from frc_mcp_nt4.capture import buffer as capture_buffer
from frc_mcp_nt4.capture.buffer import SampleBuffer
from frc_mcp_nt4.capture.types import LossWindow, TimestampBarrier
from frc_mcp_wpilib.reduce.segments import Window, split_segments


class OldBuffer:
    def __init__(self, rate_hz, all_changes):
        self.rate_hz = rate_hz
        self.all_changes = all_changes
        self.dropped = 0
        self.coalesced = 0
        self.ignored_topics = 0
        self.total = 0
        self.bytes = 0
        self.rate_window = {}
        self.unordered_windows = set()
        self.samples = {}
        self.sizes = {}
        self.types = {}
        self.dropped_by_topic = {}
        self.evicted_frontiers = {}
        self.drop_barriers = {}
        self.topic_barriers = {}
        self.timestamp_barriers = {}
        self.coalesced_by_topic = {}

    def _count_dropped(self, name):
        self.dropped += 1
        self.dropped_by_topic[name] = self.dropped_by_topic.get(name, 0) + 1

    def _count_coalesced(self, name):
        self.coalesced += 1
        self.coalesced_by_topic[name] = self.coalesced_by_topic.get(name, 0) + 1

    def _drop_size(self, name, last):
        self.bytes -= self.sizes[name].pop() if last else self.sizes[name].popleft()
        self.total -= 1

    def _shift_barriers(self, name):
        for barriers in (self.drop_barriers, self.topic_barriers):
            if name in barriers:
                barriers[name] = [LossWindow(max(0, w.position - 1), w.start, w.frontier) for w in barriers[name]]
        if name in self.timestamp_barriers:
            barrier = self.timestamp_barriers[name]
            self.timestamp_barriers[name] = TimestampBarrier(max(0, barrier.position - 1), barrier.frontier, barrier.updates)

    def evict(self, name):
        t_ms, _, reliable = self.samples[name].popleft()
        if reliable:
            self.evicted_frontiers[name] = max(t_ms, self.evicted_frontiers.get(name, t_ms))
        self._drop_size(name, last=False)
        self._shift_barriers(name)
        self._count_dropped(name)

    @staticmethod
    def _newest_reliable(buf):
        return max((row[0] for row in buf if row[2]), default=0.0)

    def topic_buffer(self, name, type_str):
        buf = self.samples.get(name)
        if buf is None:
            if len(self.samples) >= capture_buffer.MAX_TOPICS:
                self.ignored_topics += 1
                return None
            buf = self.samples[name] = deque()
            self.sizes[name] = deque()
            self.types[name] = type_str
        elif type_str:
            if self.types[name] and self.types[name] != type_str:
                while buf:
                    self.evict(name)
            self.types[name] = type_str
        return buf

    def note_unpublish(self, name, now_ms):
        buf = self.samples[name]
        if not math.isfinite(now_ms):
            count = self.timestamp_barriers.get(name, TimestampBarrier(0, 0.0, 0)).updates
            self.timestamp_barriers[name] = TimestampBarrier(len(buf), math.inf, count + 1)
        else:
            SampleBuffer.add_barrier(self.topic_barriers, name, len(buf), now_ms, max(now_ms, self._newest_reliable(buf)))
        self.unordered_windows.add(name)

    def note_unplaceable(self, name, arrival_ms):
        previous = self.timestamp_barriers.get(name, TimestampBarrier(0, 0.0, 0))
        self.timestamp_barriers[name] = TimestampBarrier(len(self.samples[name]), max(previous.frontier, arrival_ms),
                                                         previous.updates + 1)

    def record(self, name, sample, size, continuity):
        if size > capture_buffer.MAX_BUFFER_BYTES:
            buf = self.samples[name]
            self._count_dropped(name)
            SampleBuffer.add_barrier(self.drop_barriers, name, len(buf), sample[0],
                                     max(sample[0], self._newest_reliable(buf)))
            self.unordered_windows.add(name)
        else:
            self._append(name, sample, size, continuity)

    def _should_coalesce(self, name, sample, same_window, continuity):
        buf = self.samples[name]
        t_ms, raw, reliable = sample
        if not (same_window and buf and reliable and buf[-1][2] and name not in self.unordered_windows):
            return False
        segments, _ = split_segments([buf[-1][:2], (t_ms, raw)], continuity())
        return len(segments) == 1 and len(segments[0]) == 2

    def _make_room(self, name, size):
        if len(self.samples[name]) >= capture_buffer.PER_TOPIC_CAP:
            self.evict(name)
        while self.total and (self.total >= capture_buffer.MAX_TOTAL or self.bytes + size > capture_buffer.MAX_BUFFER_BYTES):
            oldest = min((n for n, rows in self.samples.items() if rows), key=lambda n: self.samples[n][0][0])
            self.evict(oldest)

    def _append(self, name, sample, size, continuity):
        buf = self.samples[name]
        t_ms = sample[0]
        win = -1 if self.all_changes else int(time.monotonic() * self.rate_hz)
        same_window = win >= 0 and self.rate_window.get(name) == win
        if not same_window:
            self.unordered_windows.discard(name)
        if win >= 0 and buf and t_ms < buf[-1][0]:
            self.unordered_windows.add(name)
        if self._should_coalesce(name, sample, same_window, continuity):
            buf.pop()
            self._drop_size(name, last=True)
            self._count_coalesced(name)
        self._make_room(name, size)
        buf.append(sample)
        self.sizes[name].append(size)
        self.bytes += size
        self.total += 1
        self.rate_window[name] = win


def _same(old, new):
    view = new.view(None)
    assert {n: list(r) for n, r in old.samples.items()} == view.samples
    assert {n: list(r) for n, r in old.sizes.items()} == {n: list(r) for n, r in new.sizes.items()}
    assert (old.total, old.bytes, old.dropped, old.coalesced, old.ignored_topics) == \
        (new.total, new.bytes, new.dropped, new.coalesced, new.ignored_topics)
    assert old.dropped_by_topic == view.dropped_by_topic and old.coalesced_by_topic == view.coalesced_by_topic
    assert old.evicted_frontiers == view.evicted_frontiers and old.types == view.types
    assert old.drop_barriers == view.drop_barriers
    assert old.topic_barriers == view.topic_barriers
    assert old.timestamp_barriers == view.timestamp_barriers


@pytest.mark.parametrize("seed", range(40))
def test_new_buffer_matches_the_original_on_random_sequences(monkeypatch, seed):
    rng = random.Random(seed)
    monkeypatch.setattr(capture_buffer, "PER_TOPIC_CAP", rng.choice([2, 3, 5, 9]))
    monkeypatch.setattr(capture_buffer, "MAX_TOTAL", rng.choice([4, 7, 12, 30]))
    monkeypatch.setattr(capture_buffer, "MAX_BUFFER_BYTES", rng.choice([500, 900, 2000]))
    monkeypatch.setattr(capture_buffer, "MAX_TOPICS", rng.choice([3, 6]))
    monkeypatch.setattr(capture_buffer, "MAX_LOSS_WINDOWS", rng.choice([2, 4, 32]))
    all_changes = rng.random() < 0.6
    rate = 1e-15  # one rate window for the whole run, so decimated captures coalesce
    old, new = OldBuffer(rate, all_changes), SampleBuffer(rate, all_changes)
    names = [f"/t{i}" for i in range(8)]
    types = ["double", "string"]
    clock = 0.0

    def continuity():
        return Window()

    for _ in range(400):
        name = rng.choice(names)
        action = rng.random()
        if action < 0.85 or name not in old.samples:
            type_str = types[0] if rng.random() < 0.93 else rng.choice([*types, ""])
            a, b = old.topic_buffer(name, type_str), new.topic_buffer(name, type_str)
            assert (a is None) == (b is None)
            if a is None:
                _same(old, new)
                continue
            clock += rng.choice([0, 1, 1, 2, 5])
            t_ms = clock if rng.random() < 0.8 else max(0.0, clock - rng.randint(1, 8))  # some out of order, some ties
            sample = (t_ms, rng.random(), rng.random() < 0.85)
            size = rng.choice([100, 150, 200, 300, 600, 3000])
            old.record(name, sample, size, continuity)
            new.record(name, sample, size, continuity)
        elif action < 0.93:
            now = rng.choice([clock, clock + 1, math.inf])
            old.note_unpublish(name, now)
            new.note_unpublish(name, now)
        else:
            old.note_unplaceable(name, clock)
            new.note_unplaceable(name, clock)
        _same(old, new)


def test_stale_heap_entries_stay_bounded(monkeypatch):
    monkeypatch.setattr(capture_buffer, "MAX_TOTAL", 10)
    buf = SampleBuffer(10.0, True)
    names = [f"/t{i}" for i in range(20)]
    for name in names:
        buf.topic_buffer(name, "double")
    for i in range(5000):
        buf.record(names[i % 20], (float(i), 0.0, True), 100, Window)
    assert buf.total == 10
    assert len(buf._heap) <= 5 * len(names) + 64


def test_global_eviction_is_fast_with_many_topics(monkeypatch):
    """200 topics, every sample evicts: the old per-sample scan took about 25 us each (5 s here)."""
    monkeypatch.setattr(capture_buffer, "MAX_TOTAL", 1000)
    buf = SampleBuffer(10.0, True)
    names = [f"/t{i}" for i in range(200)]
    for name in names:
        buf.topic_buffer(name, "double")
    start = time.perf_counter()
    for i in range(200_000):
        buf.record(names[i % 200], (float(i), 1.0, True), 150, Window)
    elapsed = time.perf_counter() - start
    assert buf.total == 1000 and buf.dropped == 199_000
    assert elapsed < 4.0, f"{elapsed:.2f}s for 200k evicting samples"

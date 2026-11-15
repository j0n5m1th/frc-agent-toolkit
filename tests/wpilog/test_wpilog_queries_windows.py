"""The window helpers in `windows` (binary searches over sorted rows) against the filters they replaced."""

import random
import time
from bisect import bisect_right
from types import SimpleNamespace
from typing import Any

import pytest

from frc_mcp_wpilib.reduce.stats import US_PER_MS
from frc_mcp_wpilog import queries, when_queries, windows
from frc_mcp_wpilog.queries import Range


def old_held_before(rows, since_ms):
    if since_ms is None:
        return None
    earlier = [r for r in rows if r[0] < since_ms]
    return earlier[-1] if earlier else None


def old_in_window(rows, since_ms, until_ms):
    if since_ms is not None:
        rows = [r for r in rows if r[0] >= since_ms]
    if until_ms is not None:
        rows = [r for r in rows if r[0] <= until_ms]
    return rows


def old_window_rows(log, name, window, origin):
    every = queries.aligned(log.rows([name])[name], origin)
    start_ms = window.since_ms if window.since_ms is not None else (log.start_us - origin) / US_PER_MS
    carried = old_held_before(every, window.since_ms)
    return ([(start_ms, carried[1])] if carried else []) + old_in_window(every, window.since_ms, window.until_ms)


def old_index(rows, at_ms, origin):
    from bisect import bisect_right
    return len(rows) if at_ms is None else bisect_right([r[0] for r in rows], origin + at_ms * US_PER_MS)


def random_rows(rng: random.Random, n: int) -> list[tuple[float, Any]]:
    times = sorted(rng.choice([0.0, 0.5, 1.0, 2.0, 2.5, 3.0, 7.0, 10.0]) + rng.randrange(0, 4) for _ in range(n))
    return [(t, i) for i, t in enumerate(times)]  # duplicate times on purpose


def bound(rng):
    return rng.choice([None, -1.0, 0.0, 1.0, 2.0, 2.5, 3.0, 5.5, 9.0, 13.0, 50.0])


def test_window_helpers_match_the_old_filters_on_random_inputs():
    rng = random.Random(11)
    for _ in range(2000):
        rows = random_rows(rng, rng.randrange(0, 30))
        since, until = bound(rng), bound(rng)
        assert windows._in_window(rows, since, until) == old_in_window(rows, since, until)
        assert windows._held_before(rows, since) == old_held_before(rows, since)


def test_window_rows_match_the_old_version_on_random_logs():
    rng = random.Random(5)
    for _ in range(500):
        raw = [(int(t * 1000) + 5_000_000, v) for t, v in random_rows(rng, rng.randrange(0, 30))]
        log = SimpleNamespace(rows=lambda names, raw=raw: {names[0]: raw}, start_us=5_000_000)
        origin = rng.choice([5_000_000, 5_000_000 + 2_000_000, 0])
        window = Range(since_ms=bound(rng), until_ms=bound(rng))
        assert when_queries._window_rows(log, "t", window, origin) == old_window_rows(log, "t", window, origin)


def test_the_index_for_a_time_matches_the_old_one():
    rng = random.Random(3)
    for _ in range(500):
        rows = [(int(t * 1000) + 5_000_000, v) for t, v in random_rows(rng, rng.randrange(0, 30))]
        at = rng.choice([None, 0.0, 1.5, 3.0, 7.0, 99.0])
        got = len(rows) if at is None else bisect_right(rows, 5_000_000 + at * US_PER_MS, key=windows._TIME)
        assert got == old_index(rows, at, 5_000_000)


@pytest.mark.parametrize("n", [600_000])
def test_a_narrow_window_over_a_huge_topic_is_far_cheaper_than_the_old_filters(n):
    rows = [(i * 0.5, i) for i in range(n)]
    since, until = n * 0.25, n * 0.25 + 500.0
    start = time.perf_counter()
    for _ in range(20):
        got = windows._in_window(rows, since, until)
        held = windows._held_before(rows, since)
    new_s = time.perf_counter() - start
    start = time.perf_counter()
    expected = old_in_window(rows, since, until)
    old_held = old_held_before(rows, since)
    old_s = (time.perf_counter() - start) * 20
    assert got == expected and held == old_held
    assert new_s < 0.5 and new_s * 20 < old_s, (new_s, old_s)

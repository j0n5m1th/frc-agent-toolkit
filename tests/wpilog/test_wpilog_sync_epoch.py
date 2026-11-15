"""The AdvantageKit clock read (`sync.epoch_diffs`) matches the old quadratic scan and is fast on a long log."""

import random
import time

from frc_mcp_wpilog import sync
from frc_mcp_wpilog.sync import _UNIX_2020_US


def old_epoch_diffs(epoch_rows, flags):
    """The implementation before the binary search, kept as the reference."""
    diffs = []
    for t, v in epoch_rows:
        ok = [f for ft, f in flags if ft <= t]
        if ok and ok[-1] and isinstance(v, int) and v > _UNIX_2020_US:
            diffs.append(v - t)
    return diffs


def random_rows(rng, n, value):
    times = sorted(rng.randrange(0, 60) for _ in range(n))  # small range: many equal timestamps
    return [(t, value(rng, t)) for t in times]


def test_matches_the_old_scan_on_random_small_inputs():
    rng = random.Random(7)
    for _ in range(300):
        epoch = random_rows(rng, rng.randrange(0, 25), lambda r, t: r.choice(
            [_UNIX_2020_US + t + 5, _UNIX_2020_US + t + 9, 5, "x", 1.5, _UNIX_2020_US - 1]))
        flags = random_rows(rng, rng.randrange(0, 8), lambda r, t: r.choice([True, False, 0, 1]))
        assert sync.epoch_diffs(epoch, flags) == old_epoch_diffs(epoch, flags)


def test_a_thirty_minute_log_is_read_in_seconds_not_minutes():
    rng = random.Random(1)
    epoch = [(i * 9_000, _UNIX_2020_US + i * 9_000 + 123) for i in range(200_000)]  # 30 minutes at 111 Hz
    flags = [(i * 36_000, rng.random() < 0.9) for i in range(50_000)]
    start = time.perf_counter()
    got = sync.epoch_diffs(epoch, flags)
    new_s = time.perf_counter() - start
    assert new_s < 3.0, new_s
    assert got and set(got) == {_UNIX_2020_US + 123}
    # the old scan on a 1/20 slice gives the same answer (it would take minutes on the whole thing)
    small_epoch, small_flags = epoch[:10_000], [f for f in flags if f[0] <= epoch[9_999][0]]
    assert sync.epoch_diffs(small_epoch, small_flags) == old_epoch_diffs(small_epoch, small_flags)

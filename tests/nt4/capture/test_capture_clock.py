"""Unit tests for the capture clock: elapsed-ms conversion, clamping and unplaceable-time rejection."""

import math
from types import SimpleNamespace

from frc_mcp_nt4.capture.capture_clock import CaptureClock


def value(time_us):
    """The minimal ntcore-value stand-in the clock reads: only `time()` is used."""
    return SimpleNamespace(time=lambda: time_us)


def rejected(clock, stamped, inst, now_us):
    """The admission sequence the recorder runs per update: the sync check first (it may set the rearm point), then
    the timestamp check."""
    return not clock.can_place_update(inst, stamped, now_us)


def test_try_elapsed_ms_needs_both_the_origin_and_the_timestamp():
    clock = CaptureClock()
    assert clock.try_elapsed_ms(1_500_000) is None  # no origin yet
    clock.start(1_000_000)
    assert clock.try_elapsed_ms(None) is None
    assert clock.try_elapsed_ms(1_000_000) == 0
    assert clock.try_elapsed_ms(1_250_000) == 250
    assert clock.try_elapsed_ms(900_000) == -100  # no clamping here: the callers decide


def test_try_run_time_ms_uses_the_local_clock_or_defers_to_the_monotonic_fallback():
    clock = CaptureClock()
    assert clock.try_run_time_ms(True, 1_000_000) is None  # no origin: the caller falls back
    clock.start(1_000_000)
    assert clock.try_run_time_ms(True, 1_300_000) == 300
    clock.stop(1_400_000)
    assert clock.try_run_time_ms(False, 9_000_000) == 400  # frozen at the stop stamp
    assert clock.try_run_time_ms(False, None) == 400  # the frozen stamp is used even without a now


def test_arrival_ms_never_freezes_and_is_infinite_without_a_clock():
    clock = CaptureClock()
    assert clock.arrival_ms(1_000_000) is math.inf
    clock.start(1_000_000)
    assert clock.arrival_ms(None) is math.inf
    assert clock.arrival_ms(1_150_000) == 150
    clock.stop(1_200_000)
    assert clock.arrival_ms(1_500_000) == 500  # arrival times keep advancing after the stop


def test_sample_time_ms_clamps_to_the_origin_and_the_arrival():
    clock = CaptureClock()
    assert clock.sample_time_ms(value(500_000), 42) == 42  # no origin: arrival time is all we have
    clock.start(1_000_000)
    assert clock.sample_time_ms(value(0), 42) == 42  # an untimestamped value keeps its arrival time
    assert clock.sample_time_ms(value(900_000), 42) == 0  # an initial snapshot is clamped to the start
    assert clock.sample_time_ms(value(1_100_000), 200) == 100  # its own timestamp, when it is the earlier one
    assert clock.sample_time_ms(value(1_100_000), 42) == 42  # never later than the arrival
    assert clock.sample_time_ms(value(9_000_000), 42) == 42  # a future timestamp is capped by the arrival too


def test_unplaceable_without_an_instance_checks_only_the_timestamps():
    clock = CaptureClock()
    assert rejected(clock, value(1_100_000), None, 1_200_000) is True  # no origin yet
    clock.start(1_000_000)
    assert rejected(clock, value(0), None, 1_200_000) is True  # missing timestamp
    assert rejected(clock, value(1_300_000), None, 1_200_000) is True  # in the future
    assert rejected(clock, value(1_100_000), None, 1_200_000) is False
    assert rejected(clock, value(1_100_000), None, None) is True  # no local clock to compare with


def test_sync_state_tracks_the_offset_and_rearms_once():
    clock = CaptureClock()
    clock.start(1_000_000)
    unsynchronized = SimpleNamespace(getServerTimeOffset=lambda: None)
    assert rejected(clock, value(1_100_000), unsynchronized, 1_200_000) is True
    assert clock.synchronized is False
    assert clock.rearm_after_us is None

    synchronized = SimpleNamespace(getServerTimeOffset=lambda: 0)
    # the first value after the clock became synchronized only establishes the rearm point
    assert rejected(clock, value(1_100_000), synchronized, 1_300_000) is True
    assert clock.synchronized is True
    assert clock.rearm_after_us == 1_300_000  # a later value must be newer than this
    assert rejected(clock, value(1_250_000), synchronized, 1_400_000) is True  # at or before the point
    assert rejected(clock, value(1_350_000), synchronized, 1_400_000) is False
    assert clock.rearm_after_us == 1_300_000  # the point is not consumed by the clock itself


def test_sync_rearm_needs_a_now():
    clock = CaptureClock()
    clock.start(1_000_000)
    clock.synchronized = False
    instance = SimpleNamespace(getServerTimeOffset=lambda: 0)
    assert rejected(clock, value(1_100_000), instance, None) is True
    assert clock.synchronized is False and clock.rearm_after_us is None


def test_rearm_point_is_infinite_without_the_local_clock():
    clock = CaptureClock()
    assert clock.rearm_point(None) is math.inf
    assert clock.rearm_point(1_234_567) == 1_234_567

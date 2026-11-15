"""Deterministic tests for capture gaps, outages, loss windows and ordering."""

import threading
import time
from types import SimpleNamespace

import ntcore
import pytest
from capture_helpers import (
    add_gap,
    ask,
    clock_rearm_after,
    clock_synchronized,
    event_query,
    event_state,
    event_states,
    feed_update,
    gap_history,
    read_gaps,
    seed_samples,
    set_clock_origin,
    set_clock_synchronized,
    set_gaps,
    set_instance,
    set_loop,
    start_gap,
    stub_loop,
    trigger_ms,
    trigger_state,
)

from frc_mcp_nt4 import clock as nt_clock
from frc_mcp_nt4.capture import (
    Capture,
    CaptureSpec,
    EventComparison,
    EventRule,
    Trigger,
)
from frc_mcp_nt4.capture import state as capture_state
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_wpilib.reduce import stats as capture_stats
from frc_mcp_wpilib.reduce.segments import Window
from frc_mcp_wpilib.reduce.stats import Shaper, _edges
from frc_mcp_wpilib.reduce.types import Gap


def test_open_and_omitted_gaps_exclude_uncertain_samples():
    rows = [(0, 0.0), (50, 10.0), (100, 0.0), (200, 10.0), (300, 0.0)]
    result = _edges(rows, "double", 5, 20, Window(gaps=[[250, None]], omitted_gap_until=75))
    assert result["rising_ms"] == [200] and result["falling_ms"] == []
    assert result["used_samples"] == 2 and result["gap_affected"]


def test_gap_reduction_preserves_new_baselines_and_reports_unknown_changes():
    rows = [(0, "a"), (10, "b"), (50, "b"), (60, "c")]
    gaps = [[20, 40]]
    changes = capture_stats.reduce_rows(rows, Shaper("string", None, 4), "changes", 20, window=Window(gaps=gaps))
    assert changes["v"] == ["a", "b", "b", "c"]
    assert changes["gap_affected"]
    summary = capture_stats.reduce_rows(rows, Shaper("string", None, 4), "summary", 20, window=Window(gaps=gaps))
    assert summary["changes"] == 2 and summary["gap_affected"]


def test_old_gap_history_is_conservatively_marked_unknown(monkeypatch):
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    monkeypatch.setattr(capture_state, "MAX_GAPS", 2)
    set_gaps(cap, [Gap(10.0, 20.0), Gap(30.0, 40.0)])
    start_gap(cap)
    assert gap_history(cap) == (1, 20.0)
    seed_samples(cap, "/a", [(0, 0.0, True), (15, 10.0, True), (25, 0.0, True), (45, 10.0, True)], "double")
    result = ask(cap, ["/a"], "edges", threshold=5)
    assert result["gap_history_unknown_until_ms"] == 20.0
    assert result["data"]["/a"]["rising_ms"] == []


def test_query_marks_gap_in_window_even_without_samples_on_both_sides():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 1
    add_gap(cap, Gap(200.0, 300.0))
    seed_samples(cap, "/a", [(100.0, 5.0, True)], "double")
    full = ask(cap, ["/a"], "summary")
    assert "constants" not in full and full["data"]["/a"]["gap_affected"]
    before = ask(cap, ["/a"], "summary", since_ms=0, until_ms=150)
    assert before["constants"] == {"/a": 5.0}
    empty = ask(cap, ["/missing"], "edges", since_ms=200, until_ms=300, threshold=5)
    assert empty["data"]["/missing"]["gap_affected"]


def test_latest_excludes_samples_in_gaps_and_reports_no_usable_value():
    rows = [(100, 1), (300, 3), (200, 2)]
    result = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "latest", 20, window=Window(gaps=[[150, 250]]))
    assert result["v"] == 3 and result["t_ms"] == 300
    assert result["gap_affected"] and result["used_samples"] == 2
    no_usable = capture_stats.reduce_rows([(200, 2)], Shaper("double", None, 4), "latest", 20, window=Window(gaps=[[150, 250]]))
    assert no_usable == {"n": 1, "used_samples": 0, "gap_affected": True}
    old_history = capture_stats.reduce_rows([(100, 1)], Shaper("double", None, 4), "latest", 20, window=Window(omitted_gap_until=150))
    assert old_history == {"n": 1, "used_samples": 0, "gap_affected": True}
    current = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "latest", 20)
    assert current["v"] == 3 and "gap_affected" not in current
    tied = capture_stats.reduce_rows([(100, 1), (100, 2)], Shaper("double", None, 4), "latest", 20)
    assert tied["v"] == 2
    delayed = capture_stats.reduce_rows([(100, 1), (1800, 3), (200, 2)], Shaper("double", None, 4), "latest", 20, window=Window(gaps=[[500, 1000]]))
    assert delayed["v"] == 3 and delayed["t_ms"] == 1800 and delayed["gap_affected"]


def test_stale_or_untimestamped_updates_do_not_rearm_after_a_gap(monkeypatch):
    local_clock = [1_300_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: local_clock[0])
    trigger = Trigger(topic="/trigger", op="==", value=True, immediate=True)
    cap = Capture("c", CaptureSpec(topics=["/trigger"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=trigger, event_rules=[ EventRule(topic="/trigger", op="==", value=True, immediate=True), ]), None)
    cap.started = time.monotonic() - 1
    set_clock_origin(cap, 1_000_000)
    add_gap(cap, Gap(200.0, 300.0))
    trigger_state(cap).after_gap = True
    event_state(cap, 0).after_gap = True
    set_loop(cap)

    def update(value, timestamp):
        if 1_000_000 <= timestamp <= 4_000_000:
            local_clock[0] = max(local_clock[0], timestamp + 1000)
        feed_update(cap, "/trigger", value, "boolean", timestamp, sample=False, rule=0, trigger=True)

    update(False, 1_150_000)  # delayed from before the gap
    update(False, 0)  # missing timestamp cannot establish a post-gap baseline
    update(False, 9_000_000)  # future timestamp is not reliable
    assert "timestamp" in cap.info()["trigger"]["waiting"]
    assert event_query(cap)["rules"][0]["waiting_for_post_gap_value"]
    update(True, 1_350_000)  # first reliable value establishes a baseline only
    update(False, 1_150_000)  # delayed older values cannot change that baseline
    update(True, 1_400_000)
    assert trigger_ms(cap) is None and event_query(cap)["observed"] == 0
    update(False, 1_450_000)
    update(True, 1_500_000)
    assert trigger_ms(cap) == 500 and event_query(cap)["observed"] == 1


def test_unsynchronized_clocks_require_fresh_baselines_for_all_capture_paths(monkeypatch):
    clock = [1_300_000]
    offset = [None]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: clock[0])
    cap = Capture("c", CaptureSpec(topics=["/start", "/end"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/start", op="==", value=True), event_rules=[ EventRule(topic="/start", op="==", value=True), EventRule(topic="/end", op="==", value=True), ]), None)
    set_clock_origin(cap, 1_000_000)
    set_instance(cap, SimpleNamespace(getServerTimeOffset=lambda: offset[0]))
    set_clock_synchronized(cap, False)
    set_loop(cap)

    def update(index, value, timestamp):
        feed_update(cap, ("/start", "/end")[index], value, "boolean", timestamp, rule=index, trigger=index == 0)

    for index in (0, 1):
        update(index, False, 1_100_000)
        update(index, True, 1_200_000)
    assert trigger_ms(cap) is None and event_query(cap)["observed"] == 0
    assert "synchronized" in event_query(cap)["rules"][0]["waiting"]
    assert ask(cap, None, "samples")["data"]["/start"]["timestamp_affected"]

    offset[0] = 0
    clock[0] = 1_400_000
    for index in (0, 1):
        update(index, False, 1_250_000)
    assert clock_rearm_after(cap) == 1_400_000
    assert trigger_ms(cap) is None and event_query(cap)["observed"] == 0

    clock[0] = 1_600_000
    for index in (0, 1):
        update(index, False, 1_500_000)
    clock[0] = 1_900_000
    update(0, True, 1_700_000)
    update(1, True, 1_800_000)
    result = ask(cap, None, "edges", event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert result["data"]["/start"]["rising_ms"] == [700]
    assert result["data"]["/start"]["timestamp_affected"]
    assert result["event_comparison"]["matched"] == 1
    assert result["event_comparison"]["pairs"][0]["delay_ms"] == 100
    assert result["trigger"]["timing_uncertain"] and result["events"]["rules"][0]["history_uncertain"]

    offset[0] = None
    clock[0] = 2_000_000
    update(0, False, 1_950_000)
    assert event_query(cap, 0)["items"][0]["timing_uncertain"]
    offset[0] = 0
    clock[0] = 2_100_000
    update(0, False, 2_050_000)
    assert clock_rearm_after(cap) == 2_100_000
    clock[0] = 2_300_000
    update(0, False, 2_200_000)
    clock[0] = 2_500_000
    update(0, True, 2_400_000)
    recovered = event_query(cap, 0)
    assert recovered["observed"] == 3 and recovered["items"][-1]["t_ms"] == 1400
    assert "timing_uncertain" not in recovered["items"][-1]


def test_capture_records_initial_clock_sync_state(monkeypatch):
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: 1_000_000)
    instance = SimpleNamespace(
        getServerTimeOffset=lambda: None,
        addConnectionListener=lambda *_args: 1,
        isConnected=lambda: True,
        removeListener=lambda *_args: None,
    )
    cap = Capture("c", CaptureSpec(topics=[], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.start(instance, stub_loop())
    assert clock_synchronized(cap) is False
    cap.stop()


@pytest.mark.parametrize("timestamp", [0, 9_000_000])
def test_unplaceable_update_invalidates_coverage_and_rearms_after_gap(timestamp, monkeypatch):
    local_clock = [1_700_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: local_clock[0])
    cap = Capture("c", CaptureSpec(topics=["/start", "/end"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/end", op="==", value=True), event_rules=[ EventRule(topic="/start", op="==", value=True), EventRule(topic="/end", op="==", value=True), ]), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)
    add_gap(cap, Gap(200, 300))
    trigger_state(cap).after_gap = True
    for state in event_states(cap):
        state.after_gap = True
    set_loop(cap)

    def update(index, value, ts):
        if 1_000_000 <= ts <= 4_000_000:
            local_clock[0] = max(local_clock[0], ts + 1000)
        feed_update(cap, ("/start", "/end")[index], value, "boolean", ts, sample=False,
                    rule=index, trigger=index == 1)

    update(0, False, 1_400_000)
    update(1, False, 1_400_000)
    update(0, True, 1_700_000)
    update(1, True, timestamp)
    result = ask(cap, None, "latest", since_ms=0, until_ms=1000, event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert result["event_comparison"]["unknown"] == 1
    assert result["event_comparison"]["no_following_event_in_window"] == 0
    assert "timestamp" in result["events"]["rules"][1]["waiting"]
    assert result["trigger_history_uncertain"]
    update(1, True, 1_800_000)
    assert trigger_ms(cap) is None and event_query(cap)["rules"][1]["observed"] == 0
    update(1, False, 1_900_000)
    update(1, True, 2_000_000)
    assert cap.info()["trigger"]["timing_uncertain"] and event_query(cap)["rules"][1]["observed"] == 1


@pytest.mark.parametrize("timestamp", [0, 9_000_000])
def test_unplaceable_update_invalidates_trigger_and_events_without_gap(timestamp, monkeypatch):
    local_clock = [1_500_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: local_clock[0])
    cap = Capture("c", CaptureSpec(topics=["/start", "/end"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/start", op="==", value=True), event_rules=[ EventRule(topic="/start", op="==", value=True), EventRule(topic="/end", op="==", value=True), ]), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)
    set_loop(cap)

    def update(index, value, ts):
        if 1_000_000 <= ts <= 4_000_000:
            local_clock[0] = max(local_clock[0], ts + 1000)
        feed_update(cap, ("/start", "/end")[index], value, "boolean", ts, sample=False,
                    rule=index, trigger=index == 0)

    update(0, False, 1_100_000)
    update(1, False, 1_100_000)
    update(0, True, 1_500_000)
    update(1, True, timestamp)
    result = ask(cap, None, "latest", since_ms=0, until_ms=1000, event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert result["event_comparison"]["unknown"] == 1
    assert result["events"]["rules"][1]["observed"] == 0
    update(0, False, timestamp)
    assert cap.info()["trigger"]["timing_uncertain"]
    with pytest.raises(NT4Error, match="trigger time is uncertain"):
        ask(cap, None, "latest", since_ms=-100, until_ms=100, rel_trigger=True)
    update(1, True, 1_800_000)
    assert event_query(cap)["rules"][1]["observed"] == 0
    update(1, False, 1_900_000)
    update(1, True, 2_000_000)
    assert event_query(cap)["rules"][1]["observed"] == 1


def test_unplaceable_update_requires_baseline_newer_than_its_arrival(monkeypatch):
    local_clock = [1_300_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: local_clock[0])
    cap = Capture("c", CaptureSpec(topics=["/start", "/end"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/end", op="==", value=True), event_rules=[ EventRule(topic="/start", op="==", value=True), EventRule(topic="/end", op="==", value=True), ]), None)
    cap.started = time.monotonic() - 3
    set_clock_origin(cap, 1_000_000)
    set_loop(cap)

    def update(index, value, ts):
        if 1_000_000 <= ts <= 4_000_000:
            local_clock[0] = max(local_clock[0], ts + 1000)
        feed_update(cap, ("/start", "/end")[index], value, "boolean", ts, sample=False,
                    rule=index, trigger=index == 1)

    update(0, False, 1_100_000)
    update(1, False, 1_100_000)
    update(1, True, 0)
    update(1, False, 1_200_000)
    update(0, True, 1_400_000)
    update(1, True, 1_500_000)
    result = ask(cap, None, "latest", since_ms=0, until_ms=1000, event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert result["event_comparison"]["matched"] == 0
    assert result["event_comparison"]["unknown"] == 1
    assert trigger_ms(cap) is None and event_query(cap)["rules"][1]["observed"] == 0
    update(0, False, 2_400_000)
    update(1, False, 2_400_000)
    update(0, True, 2_500_000)
    update(1, True, 2_600_000)
    result = ask(cap, None, "latest", since_ms=1400, until_ms=2800, event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert result["event_comparison"]["matched"] == 1
    assert cap.info()["trigger"]["timing_uncertain"]


def test_connect_callback_during_initial_state_check_closes_gap():
    cap = Capture("c", CaptureSpec(topics=[], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    entered = threading.Event()
    connected = SimpleNamespace(is_=lambda flag: flag == ntcore.EventFlags.kConnected)

    class Instance:
        def getServerTimeOffset(self):
            return 0

        def addConnectionListener(self, _immediate, callback):
            self.callback = callback
            return 1

        def isConnected(self):
            def deliver():
                entered.set()
                self.callback(connected)

            self.worker = threading.Thread(target=deliver)
            self.worker.start()
            assert entered.wait(1)
            return False

        def removeListener(self, _handle):
            pass

    instance = Instance()
    cap.start(instance, stub_loop())
    instance.worker.join(timeout=1)
    assert not instance.worker.is_alive()
    gaps = read_gaps(cap)
    assert len(gaps) == 1 and gaps[0].end is not None
    cap.stop()

"""Deterministic tests for topic loss: unpublish/republish, delayed values and folded loss windows."""


import time
from types import SimpleNamespace

import ntcore
import pytest
from capture_helpers import (
    add_gap,
    after_latest_gap,
    ask,
    buffer_stats,
    drop_barriers,
    event_query,
    event_state,
    evict,
    feed,
    feed_rule,
    feed_trigger,
    feed_update,
    notify_rule,
    notify_sample,
    notify_trigger,
    notify_update,
    read_gaps,
    run_time_ms,
    sample_time_ms,
    seed_samples,
    seed_sizes,
    set_clock_origin,
    set_drop_barriers,
    set_last_gap,
    set_loop,
    set_topic_barriers,
    start_gap,
    stub_loop,
    topic_barriers,
    trigger_ms,
    trigger_state,
    unpublish_event,
)

from frc_mcp_nt4 import clock as nt_clock
from frc_mcp_nt4.capture import (
    Capture,
    CaptureSpec,
    EventComparison,
    EventRule,
    Trigger,
)
from frc_mcp_nt4.capture import buffer as capture_buffer
from frc_mcp_nt4.capture import recorder as capture
from frc_mcp_nt4.capture.buffer import SampleBuffer
from frc_mcp_nt4.capture.types import LossWindow
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_wpilib import conditions
from frc_mcp_wpilib.reduce.segments import overlaps_outage
from frc_mcp_wpilib.reduce.types import Gap


def test_capture_gaps_and_samples_share_the_nt_local_clock(monkeypatch):
    monotonic = [10.0]
    local_us = [1_000_000]
    monkeypatch.setattr(capture.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: local_us[0])

    class Instance:
        def getServerTimeOffset(self):
            return 0

        def addConnectionListener(self, _immediate, callback):
            self.callback = callback
            return 1

        def isConnected(self):
            return True

        def removeListener(self, _handle):
            pass

    cap = Capture("c", CaptureSpec(topics=[], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    instance = Instance()
    monotonic[0] = 10.25
    local_us[0] = 1_250_000
    cap.start(instance, stub_loop())
    monotonic[0] = 11.0
    local_us[0] = 1_300_000
    instance.callback(SimpleNamespace(is_=lambda flag: flag == ntcore.EventFlags.kDisconnected))
    local_us[0] = 1_340_000
    instance.callback(SimpleNamespace(is_=lambda flag: flag == ntcore.EventFlags.kConnected))
    assert read_gaps(cap) == [Gap(50, 90)]
    local_us[0] = 1_400_000
    value = SimpleNamespace(time=lambda: 1_360_000)
    assert after_latest_gap(cap, value) and sample_time_ms(cap, value) == 110
    cap.stop()
    local_us[0] = 3_000_000
    assert cap.info()["elapsed_s"] == 0.1
    assert run_time_ms(cap) == 150


def test_topic_unpublish_breaks_capture_trigger_and_event_continuity(monkeypatch):
    clock = [1_100_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: clock[0])
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/a", op=">", value=5), event_rules=[EventRule(topic="/a", op=">", value=5)]), None)
    set_clock_origin(cap, 1_000_000)
    set_loop(cap)

    def update(ms, value):
        clock[0] = 1_000_000 + ms * 1000
        notify_update(cap, "/a", value, "double", clock[0], rule=0, trigger=True)

    update(100, 0)
    update(200, 1)
    clock[0] = 1_300_000
    lost = unpublish_event("/a", check_flag=True)
    notify_sample(cap, lost)
    notify_rule(cap, 0, lost)
    notify_trigger(cap, lost)
    update(400, 10)
    assert trigger_ms(cap) is None and event_query(cap)["observed"] == 0
    edges = ask(cap, ["/a"], "edges", threshold=5)["data"]["/a"]
    assert edges["rising_ms"] == [] and edges["topic_affected"]
    earlier = ask(cap, ["/a"], "time_weighted", until_ms=250)["data"]["/a"]
    assert earlier["covered_ms"] == 100 and earlier["mean"] == 0
    update(500, 0)
    update(600, 10)
    edges = ask(cap, ["/a"], "edges", threshold=5)["data"]["/a"]
    assert edges["rising_ms"] == [600] and edges["falling_ms"] == [500]
    assert event_query(cap)["observed"] == 1 and cap.info()["trigger"]["timing_uncertain"]


def test_republished_topic_type_change_discards_incompatible_history(monkeypatch):
    clock = [1_100_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: clock[0])
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    set_clock_origin(cap, 1_000_000)

    def update(ms, value, type_str):
        clock[0] = 1_000_000 + ms * 1000
        feed(cap, "/a", value, type_str, t_us=clock[0])

    update(100, "old", "string")
    clock[0] = 1_200_000
    notify_sample(cap, unpublish_event("/a"))
    update(300, 1.0, "double")
    update(400, 2.0, "double")
    result = ask(cap, ["/a"], "time_weighted")["data"]["/a"]
    assert "type" not in result and result["mean"] == 1 and result["covered_ms"] == 100
    assert result["topic_affected"] and buffer_stats(cap).dropped == 1


def test_queued_value_after_unpublication_does_not_clear_topic_type(monkeypatch):
    clock = [1_100_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: clock[0])
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    set_clock_origin(cap, 1_000_000)
    type_str = ["double"]

    def update(value):
        feed(cap, "/a", value, type_str[0], t_us=clock[0])

    update(1.0)
    clock[0] = 1_200_000
    type_str[0] = ""
    update(2.0)
    result = ask(cap, ["/a"], "summary")["data"]["/a"]
    assert "type" not in result and result["first"] == 1 and result["last"] == 2
    assert buffer_stats(cap).dropped == 0


def test_delayed_pre_gap_values_invalidate_old_timing_without_resetting_new_baselines():
    cap = Capture("c", CaptureSpec(topics=["/start", "/end"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/start", op="==", value=True), event_rules=[ EventRule(topic="/start", op="==", value=True), EventRule(topic="/end", op="==", value=True), ]), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)
    set_loop(cap)

    def update(index, value, ms):
        feed_update(cap, ("/start", "/end")[index], value, "boolean", 1_000_000 + ms * 1000, sample=False,
                    rule=index, trigger=index == 0)

    update(0, False, 10)
    update(1, False, 10)
    update(0, True, 100)
    update(1, True, 500)
    start_gap(cap)
    set_last_gap(cap, Gap(600, 700))
    update(0, False, 800)
    update(1, False, 800)
    update(1, True, 300)
    update(0, True, 50)
    assert cap.info()["trigger"]["timing_uncertain"]
    assert cap.info()["trigger_out_of_order_updates"] == 1
    old = ask(cap, None, "latest", since_ms=0, until_ms=550, event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert old["event_comparison"]["unknown"] == 1
    update(0, True, 900)
    update(1, True, 950)
    new = ask(cap, None, "latest", since_ms=750, until_ms=1100, event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert new["event_comparison"]["matched"] == 1
    assert new["events"]["rules"][1]["out_of_order_updates"] == 1
    items = event_query(cap, 0)["items"]
    assert [item.get("timing_uncertain", False) for item in items] == [True, True, False, False]


@pytest.mark.parametrize("before_firing", [True, False])
def test_delayed_pre_gap_value_invalidates_post_gap_first_trigger(before_firing):
    cap = Capture("c", CaptureSpec(topics=["/trigger"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/trigger", op="==", value=True)), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)
    set_loop(cap)
    add_gap(cap, Gap(600, 700))
    trigger_state(cap).after_gap = True

    def update(value, ms):
        feed_trigger(cap, value, "boolean", 1_000_000 + ms * 1000, name="/trigger")

    update(False, 800)
    if before_firing:
        update(True, 300)
        assert cap.info()["trigger_history_uncertain"]
    update(True, 900)
    if not before_firing:
        update(True, 300)
    assert cap.info()["trigger"] == {"fired_ms": 900, "timing_uncertain": True}
    assert cap.info()["trigger_out_of_order_updates"] == 1
    with pytest.raises(NT4Error, match="trigger time is uncertain"):
        ask(cap, None, "latest", since_ms=-100, until_ms=100, rel_trigger=True)


def test_queued_value_after_unpublication_keeps_trigger_and_rule_types():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/t", op=">", value=5), event_rules=[EventRule(topic="/r", op=">", value=5)]), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)
    type_str = ["double"]
    feed_trigger(cap, ntcore.Value.makeDouble(1.0), type_str[0], name="/t")
    feed_rule(cap, 0, ntcore.Value.makeDouble(1.0), type_str[0], name="/t")
    type_str[0] = ""
    feed_trigger(cap, ntcore.Value.makeDouble(10.0), type_str[0], name="/t")
    feed_rule(cap, 0, ntcore.Value.makeDouble(10.0), type_str[0], name="/t")
    assert trigger_ms(cap) is not None and trigger_state(cap).fired_type == "double" and not trigger_state(cap).error
    assert not event_state(cap, 0).error


def test_queued_struct_value_after_unpublication_keeps_field_extraction(monkeypatch):
    monkeypatch.setattr(conditions, "decode_struct_strict", lambda _typ, data: {"x": data[0]})
    cap = Capture("c", CaptureSpec(topics=["/struct"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/struct", op=">", value=5, field="x"), event_rules=[EventRule(topic="/struct", op=">", value=5, field="x")]), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)
    type_str = ["struct:Late"]

    def update(value):
        feed_update(cap, "/struct", ntcore.Value.makeRaw(bytes([value])), type_str[0], sample=False, rule=0,
                    trigger=True)

    update(1)
    type_str[0] = ""
    update(10)
    assert not event_state(cap, 0).error and not trigger_state(cap).error
    assert trigger_ms(cap) is not None


def test_repeated_unpublications_only_discard_data_around_each_loss(monkeypatch):
    clock = [1_100_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: clock[0])
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    set_clock_origin(cap, 1_000_000)

    def update(at_ms, value):
        clock[0] = 1_000_000 + at_ms * 1000
        feed(cap, "/a", value, "double", t_us=clock[0])

    def unpublish(at_ms):
        clock[0] = 1_000_000 + at_ms * 1000
        notify_sample(cap, unpublish_event("/a"))

    for at, v in ((100, 0), (200, 10), (250, 0)):
        update(at, v)
    unpublish(300)
    for at, v in ((400, 0), (450, 10), (500, 0)):
        update(at, v)
    unpublish(800)
    update(900, 0)
    update(950, 10)
    data = ask(cap, ["/a"], "edges", threshold=5)["data"]["/a"]
    assert any(400 <= t <= 500 for t in data["rising_ms"] + data["falling_ms"])


def test_loss_windows_fold_conservatively_and_shift_on_eviction():
    barriers: dict = {}
    for i in range(capture_buffer.MAX_LOSS_WINDOWS + 1):
        SampleBuffer.add_barrier(barriers, "/a", i, 100.0 * i, 100.0 * i + 10)
    windows = barriers["/a"]
    assert len(windows) == capture_buffer.MAX_LOSS_WINDOWS
    assert windows[0] == LossWindow(1, 0.0, 110.0)  # oldest two folded: later index, earliest start, latest frontier
    assert windows[1] == LossWindow(2, 200.0, 210.0)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    seed_samples(cap, "/a", [(1.0, 0, True), (2.0, 0, True)])
    seed_sizes(cap, "/a", [1, 1])
    set_topic_barriers(cap, "/a", [LossWindow(0, 5.0, 6.0), LossWindow(2, 7.0, 8.0)])
    set_drop_barriers(cap, "/a", [LossWindow(1, 5.0, 6.0)])
    evict(cap, "/a")
    assert topic_barriers(cap, "/a") == [LossWindow(0, 5.0, 6.0), LossWindow(1, 7.0, 8.0)]
    assert drop_barriers(cap, "/a") == [LossWindow(0, 5.0, 6.0)]


def test_overlaps_outage_is_a_closed_interval_test():
    closed = [(100.0, 200.0)]
    assert overlaps_outage(closed, None, 150.0, 160.0)  # inside
    assert overlaps_outage(closed, None, 200.0, 300.0)  # touching the end counts
    assert overlaps_outage(closed, None, 50.0, 100.0)  # touching the start counts
    assert not overlaps_outage(closed, None, 201.0, 300.0)
    assert overlaps_outage([(100.0, None)], None, 900.0, 950.0)  # open outage reaches forever
    assert not overlaps_outage([(100.0, None)], None, 50.0, 99.0)
    assert overlaps_outage([], 75.0, 10.0, 20.0)  # starts at or before the omitted samples
    assert not overlaps_outage([], 75.0, 80.0, 90.0)
    assert not overlaps_outage([], None, 0.0, 1000.0)

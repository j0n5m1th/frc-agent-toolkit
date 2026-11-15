"""Deterministic tests for capture capture triggers."""

import threading
import time

import ntcore
import pytest
from capture_helpers import (
    advance_clock,
    ask,
    clear_handler_types,
    close_last_gap,
    event_query,
    event_state,
    feed_rule,
    feed_trigger,
    feed_update,
    lock,
    set_clock_origin,
    set_loop,
    start_gap,
    trigger_ms,
    trigger_state,
)

from frc_mcp_nt4.capture import (
    Capture,
    CaptureSpec,
    EventComparison,
    EventRule,
    Trigger,
)
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_wpilib import conditions
from frc_mcp_wpilib.schema.structs import SchemaError


def _no_schema(_typ, _data):
    """Stand-in for a strict decode while the struct schema has not arrived yet."""
    raise SchemaError("no schema for 'struct:Late': the server has not announced it (yet)")


def test_trigger_rearms_after_reconnect_without_inventing_a_crossing():
    trig = Trigger(topic="/trigger", op="==", value=True, immediate=True)
    cap = Capture("c", CaptureSpec(topics=["/trigger"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=trig), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)

    def update(value):
        feed_trigger(cap, value, "boolean")

    update(False)
    start_gap(cap)
    assert cap.info()["trigger_history_uncertain"]
    update(True)  # updates delivered while disconnected are not evidence of a crossing
    close_last_gap(cap)
    advance_clock()
    update(True)  # first post-reconnect value establishes a new baseline, even with immediate=True
    assert trigger_ms(cap) is None
    update(False)
    update(True)
    assert trigger_ms(cap) is not None
    assert cap.info()["trigger"]["timing_uncertain"]
    with pytest.raises(NT4Error, match="trigger time is uncertain"):
        ask(cap, None, "latest", since_ms=-100, until_ms=100, rel_trigger=True)


def test_changed_trigger_uses_fresh_baseline_after_reconnect():
    trigger = Trigger(topic="/trigger", op="changed", immediate=True)
    cap = Capture("c", CaptureSpec(topics=["/trigger"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=trigger), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)

    def update(value):
        feed_trigger(cap, value, "double")

    update(0)
    start_gap(cap)
    close_last_gap(cap)
    advance_clock()
    update(1)
    assert trigger_ms(cap) is None
    update(2)
    assert trigger_ms(cap) is not None


def test_gap_after_trigger_does_not_invalidate_earlier_firing():
    cap = Capture("c", CaptureSpec(topics=["/trigger"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/trigger", op="==", value=True)), None)
    trigger_state(cap).fired_ms = 100
    start_gap(cap)
    assert cap.info()["trigger"] == {"fired_ms": 100}


def test_nested_nan_does_not_fire_array_trigger_or_event_rule():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/a", op="!=", value=[0.0, 0.0]), event_rules=[EventRule(topic="/a", op="!=", value=[0.0, 0.0])]), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)

    def update(value):
        feed_update(cap, "/a", value, "double[]", sample=False, rule=0, trigger=True)

    update([0, 0])
    update([float("nan"), 0])
    assert trigger_ms(cap) is None and event_query(cap)["observed"] == 0
    assert "NaN" in event_query(cap)["rules"][0]["waiting"]
    update([0, 0])
    update([1, 0])
    assert cap.info()["trigger"]["timing_uncertain"] and event_query(cap)["observed"] == 1


def test_struct_rule_and_trigger_recover_when_schema_becomes_available(monkeypatch):
    schema_available = False
    monkeypatch.setattr(conditions, "decode_struct_strict",
                        lambda _typ, data: {"x": data[0]} if schema_available else _no_schema(_typ, data))
    cap = Capture("c", CaptureSpec(topics=["/struct"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/struct", op=">", value=5, field="x", immediate=True), event_rules=[EventRule(topic="/struct", op=">", value=5, field="x", immediate=True)]), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)

    def update(value):
        feed_update(cap, "/struct", bytes([value]) if isinstance(value, int) else value, "struct:Late", sample=False, rule=0, trigger=True)

    update(10)
    assert "Cannot decode" in event_query(cap)["rules"][0]["waiting"]
    assert "Cannot decode" in cap.info()["trigger"]["waiting"]
    schema_available = True
    update(11)
    assert event_query(cap)["observed"] == 0 and trigger_ms(cap) is None
    assert "waiting" not in event_query(cap)["rules"][0]
    assert event_query(cap)["rules"][0]["history_uncertain"]
    update(0)
    update(10)
    assert event_query(cap)["observed"] == 1 and trigger_ms(cap) is not None


def test_undecodable_trigger_update_leaves_later_firing_uncertain(monkeypatch):
    schema_available = True
    monkeypatch.setattr(conditions, "decode_struct_strict",
                        lambda _typ, data: {"x": data[0]} if schema_available else _no_schema(_typ, data))
    cap = Capture("c", CaptureSpec(topics=["/struct"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/struct", op=">", value=5, field="x")), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)

    def update(value):
        feed_trigger(cap, bytes([value]), "struct:Late", name="/struct")

    update(0)
    schema_available = False
    update(10)
    assert cap.info()["trigger_history_uncertain"] and "waiting" in cap.info()["trigger"]
    schema_available = True
    update(0)
    update(10)
    assert cap.info()["trigger"]["timing_uncertain"] and "waiting" not in cap.info()["trigger"]
    with pytest.raises(NT4Error, match="trigger time is uncertain"):
        ask(cap, None, "latest", since_ms=-100, until_ms=100, rel_trigger=True)


def test_malformed_schema_waits_and_recovers_for_trigger_and_event_rule(monkeypatch):
    from frc_mcp_wpilib.schema.structs import Registry

    registry = Registry()
    registry.add("/.schema/struct:Late", "uint8 x")
    monkeypatch.setattr(conditions, "decode_struct_strict", registry.decode_strict)
    cap = Capture("c", CaptureSpec(topics=["/struct"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/struct", op=">", value=5, field="x"), event_rules=[EventRule(topic="/struct", op=">", value=5, field="x")]), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)

    def update(value):
        feed_update(cap, "/struct", bytes([value]) if isinstance(value, int) else value, "struct:Late", sample=False, rule=0, trigger=True)

    update(0)
    registry.add("/.schema/struct:Late", "enum{a=} uint8 x")
    update(10)
    assert "Cannot decode" in event_query(cap)["rules"][0]["waiting"]
    assert cap.info()["trigger_history_uncertain"]
    registry.add("/.schema/struct:Late", "uint8 x")
    update(0)
    update(10)
    assert event_query(cap)["observed"] == 1 and cap.info()["trigger"]["timing_uncertain"]


def test_out_of_order_rule_values_invalidate_affected_timing_and_rearm():
    cap = Capture("c", CaptureSpec(topics=["/start", "/end"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[ EventRule(topic="/start", op="==", value=True), EventRule(topic="/end", op="==", value=True), ]), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)

    def update(index, value, ms):
        feed_rule(cap, index, value, "boolean", t_us=1_000_000 + ms * 1000)

    update(0, False, 10)
    update(1, False, 10)
    update(0, True, 100)
    update(1, True, 500)
    update(1, False, 800)
    update(1, True, 300)
    events = event_query(cap)
    assert events["observed"] == 2
    assert events["rules"][1]["out_of_order_updates"] == 1
    assert events["rules"][1]["waiting_for_ordered_value"]
    update(1, True, 400)
    update(1, True, 800)
    assert event_query(cap)["rules"][1]["out_of_order_updates"] == 3
    result = ask(cap, None, "latest", since_ms=0, until_ms=1000, event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert result["event_comparison"]["matched"] == 0 and result["event_comparison"]["unknown"] == 1
    update(1, True, 900)  # fresh baseline, not a crossing
    assert event_query(cap)["observed"] == 2
    update(1, False, 1000)
    update(1, True, 1100)
    assert event_query(cap)["observed"] == 3


def test_out_of_order_start_rule_invalidates_previously_observed_start():
    cap = Capture("c", CaptureSpec(topics=["/start", "/end"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[ EventRule(topic="/start", op="==", value=True), EventRule(topic="/end", op="==", value=True), ]), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)

    def update(index, value, ms):
        feed_rule(cap, index, value, "boolean", t_us=1_000_000 + ms * 1000)

    update(0, False, 10)
    update(1, False, 10)
    update(0, True, 100)
    update(1, True, 150)
    update(0, False, 200)
    update(0, True, 50)
    result = ask(cap, None, "latest", since_ms=0, until_ms=1000, event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert result["event_comparison"]["matched"] == 0
    assert result["event_comparison"]["unknown"] == 1


def test_trigger_status_is_snapshotted_under_capture_lock(monkeypatch):
    cap = Capture("c", CaptureSpec(topics=["/trigger"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/trigger", op="==", value=True)), None)
    trigger_state(cap).history_uncertain = True
    status_reading = threading.Event()
    release_status = threading.Event()
    writer_started = threading.Event()
    writer_done = threading.Event()
    results = []

    def elapsed():
        assert lock(cap).locked()
        status_reading.set()
        assert release_status.wait(3)
        return 200

    monkeypatch.setattr(cap, "_run_time_ms", elapsed)
    reader = threading.Thread(target=lambda: results.append(cap.info()))

    def fire():
        writer_started.set()
        with lock(cap):
            trigger_state(cap).fired_ms = 150
        writer_done.set()

    writer = threading.Thread(target=fire)
    reader.start()
    try:
        assert status_reading.wait(3)
        writer.start()
        assert writer_started.wait(3)
        assert not writer_done.wait(0.05)
    finally:
        release_status.set()
        reader.join(3)
        if writer.ident is not None:
            writer.join(3)
    assert not reader.is_alive() and not writer.is_alive()
    assert results[0]["trigger"] == "armed" and results[0]["trigger_history_uncertain"]
    assert cap.info()["trigger"] == {"fired_ms": 150, "timing_uncertain": True}
    assert ask(cap, None, "latest")["trigger"]["timing_uncertain"]


def test_out_of_order_trigger_values_do_not_create_crossings():
    cap = Capture("c", CaptureSpec(topics=["/trigger"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/trigger", op="==", value=True, immediate=True)), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)
    set_loop(cap)
    trigger_state(cap).after_gap = True

    def update(value, ms):
        feed_trigger(cap, value, "boolean", t_us=1_000_000 + ms * 1000)

    update(True, 500)  # reconnect baseline; immediate must not fire
    update(False, 800)
    update(True, 300)
    assert trigger_ms(cap) is None
    assert cap.info()["trigger"]["state"] == "waiting_for_ordered_value"
    assert cap.info()["trigger_out_of_order_updates"] == 1
    update(True, 900)
    assert trigger_ms(cap) is None
    update(False, 1000)
    update(True, 1100)
    assert trigger_ms(cap) == 1100
    assert cap.info()["trigger"]["timing_uncertain"]


@pytest.mark.parametrize("timestamps", [(900_000, 800_000), (800_000, 900_000)])
def test_pre_capture_updates_are_not_trigger_or_event_crossings(timestamps):
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/a", op="==", value=True), event_rules=[EventRule(topic="/a", op="==", value=True)]), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)
    set_loop(cap)

    def update(value, ts):
        feed_update(cap, "/a", value, "boolean", t_us=ts, rule=0, trigger=True)

    update(False, timestamps[0])
    update(True, timestamps[1])
    assert trigger_ms(cap) is None and event_query(cap)["observed"] == 0
    assert ask(cap, ["/a"], "samples")["data"]["/a"]["timestamp_affected"]
    if timestamps[1] < timestamps[0]:
        assert cap.info()["trigger_out_of_order_updates"] == 1
        assert event_query(cap)["rules"][0]["out_of_order_updates"] == 1
    update(False, 1_100_000)
    update(True, 1_200_000)
    assert trigger_ms(cap) == 200 and event_query(cap)["observed"] == 1


def test_delayed_value_before_trigger_makes_later_first_firing_uncertain():
    cap = Capture("c", CaptureSpec(topics=["/trigger"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/trigger", op="==", value=True)), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)
    set_loop(cap)

    def update(value, ms):
        feed_trigger(cap, value, "boolean", t_us=1_000_000 + ms * 1000)

    update(False, 100)
    update(False, 800)
    update(True, 300)
    assert cap.info()["trigger_history_uncertain"]
    update(False, 900)
    update(True, 1000)
    assert cap.info()["trigger"] == {"fired_ms": 1000, "timing_uncertain": True}
    with pytest.raises(NT4Error, match="trigger time is uncertain"):
        ask(cap, None, "latest", since_ms=-100, until_ms=100, rel_trigger=True)


def test_out_of_order_update_after_trigger_marks_time_uncertain_without_refiring():
    cap = Capture("c", CaptureSpec(topics=["/trigger"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/trigger", op="==", value=True)), None)
    cap.started = time.monotonic() - 2
    set_clock_origin(cap, 1_000_000)
    scheduled = []
    set_loop(cap, scheduled)

    def update(value, ms):
        feed_trigger(cap, value, "boolean", t_us=1_000_000 + ms * 1000)

    update(False, 100)
    update(True, 500)
    update(False, 700)
    update(True, 600)
    assert cap.info()["trigger"] == {"fired_ms": 500}
    update(True, 300)
    assert cap.info()["trigger"] == {"fired_ms": 500, "timing_uncertain": True}
    assert cap.info()["trigger_out_of_order_updates"] == 2
    assert len(scheduled) == 1
    with pytest.raises(NT4Error, match="trigger time is uncertain"):
        ask(cap, None, "latest", since_ms=-100, until_ms=100, rel_trigger=True)
    assert ask(cap, None, "latest")["trigger"]["timing_uncertain"]


def test_trigger_and_rule_errors_recover_on_a_later_valid_value(monkeypatch):
    monkeypatch.setattr(conditions, "decode_struct_strict",
                        lambda typ, data: {"x": float(data[0])} if typ.startswith("struct:") else _no_schema(typ, data))
    cap = Capture("c", CaptureSpec(topics=["/s"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/s", op=">", value=5, field="x"), event_rules=[EventRule(topic="/s", op=">", value=5, field="x")]), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)
    type_str = ["double"]  # a non-struct type makes `field` an error until the topic is republished as a struct

    def update(value):
        feed_update(cap, "/s", bytes([value]), type_str[0], sample=False, rule=0, trigger=True)

    update(1)
    assert trigger_state(cap).error and event_state(cap, 0).error
    type_str[0] = "struct:P"
    clear_handler_types(cap)
    update(1)
    assert not trigger_state(cap).error and not event_state(cap, 0).error
    assert event_state(cap, 0).history_uncertain and "error" not in str(cap.info()["trigger"])
    update(10)
    assert trigger_ms(cap) is not None and event_query(cap)["observed"] == 1

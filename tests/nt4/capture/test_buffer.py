"""Deterministic tests for capture sample buffers: eviction, caps, coalescing and logging."""

import threading
import time
from types import SimpleNamespace

import ntcore
import pytest
from capture_helpers import (
    add_gap,
    ask,
    attach_log,
    buffer_stats,
    drop_barrier_position,
    evicted_frontier,
    feed,
    lock,
    log_file,
    query,
    retained,
    retained_count,
    retained_times,
    set_clock_origin,
    set_instance,
    set_last_update_time,
)
from wpiutil.log import DataLogReader

from frc_mcp_nt4 import clock as nt_clock
from frc_mcp_nt4.capture import Capture, CaptureSpec
from frc_mcp_nt4.capture import buffer as capture_buffer
from frc_mcp_nt4.capture import log as capture_log
from frc_mcp_nt4.capture import recorder as capture
from frc_mcp_nt4.capture import types as capture_types
from frc_mcp_wpilib.reduce.types import Gap


def test_stop_waits_for_in_flight_callback_lock():
    cap = Capture("c", CaptureSpec(topics=[], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    entered = threading.Event()

    def stop():
        entered.set()
        cap.stop()

    with lock(cap):
        worker = threading.Thread(target=stop)
        worker.start()
        assert entered.wait(1)
        time.sleep(0.01)
        assert cap.active
    worker.join(timeout=1)
    assert not worker.is_alive() and not cap.active


def test_byte_budget_evicts_across_topics_and_drops_oversized_values(monkeypatch):
    cap = Capture("c", CaptureSpec(topics=["/a", "/b", "/c"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    sample = "x" * 100
    monkeypatch.setattr(capture_buffer, "MAX_BUFFER_BYTES", capture_types.sample_size(sample) * 2)
    for name in ("/a", "/b", "/c"):
        feed(cap, name, sample, "string")
    stats = buffer_stats(cap)
    assert stats.bytes <= capture_buffer.MAX_BUFFER_BYTES
    assert stats.total == 2 and stats.dropped == 1
    assert not retained(cap, "/a") and retained(cap, "/b") and retained(cap, "/c")
    feed(cap, "/c", "x" * 10_000, "string")
    stats = buffer_stats(cap)
    assert stats.dropped == 2 and stats.total == 2
    assert query(cap)["dropped"] == 2


@pytest.mark.parametrize("limit", ["PER_TOPIC_CAP", "MAX_TOTAL"])
def test_evicted_timestamp_frontier_prevents_false_intervals(monkeypatch, limit):
    monkeypatch.setattr(capture_buffer, limit, 2)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 3
    set_clock_origin(cap, 1_000_000)

    def update(ms, value):
        feed(cap, "/a", value, "double", t_us=1_000_000 + ms * 1000)

    for ms, value in [(100, 0), (300, 10), (200, 0), (400, 10)]:
        update(ms, value)
    assert retained_times(cap, "/a") == [200, 400]
    assert evicted_frontier(cap, "/a") == 300

    def result(reduction, **kwargs):
        return ask(cap, ["/a"], reduction, **kwargs)["data"]["/a"]

    edges = result("edges", threshold=5)
    assert edges["rising_ms"] == [] and edges["order_affected"]
    changes = result("changes")
    assert changes["t_ms"] == [400] and changes["order_affected"]
    summary = result("summary")
    assert summary["const"] == 10 and summary["order_affected"]
    weighted = result("time_weighted")
    assert weighted["covered_ms"] == 0 and "mean" not in weighted
    assert weighted["out_of_order_updates"] == 1
    update(500, 0)
    recovered = result("time_weighted")
    assert recovered["covered_ms"] == 100 and recovered["mean"] == 10
    assert result("edges", threshold=5)["falling_ms"] == [500]


def test_eviction_frontier_prevents_false_complete_runs(monkeypatch):
    monkeypatch.setattr(capture_buffer, "PER_TOPIC_CAP", 3)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 3
    set_clock_origin(cap, 1_000_000)
    for ms, value in [(100, "a"), (300, "b"), (200, "a"), (400, "c"), (500, "d")]:
        feed(cap, "/a", value, "string", t_us=1_000_000 + ms * 1000)
    result = ask(cap, ["/a"], "durations")["data"]["/a"]
    assert result["complete_runs"] == 0 and result["order_affected"]


@pytest.mark.parametrize("all_changes", [True, False])
def test_oversized_dropped_update_breaks_reduction_continuity(monkeypatch, all_changes):
    clock = [10.1]
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(capture_buffer, "MAX_BUFFER_BYTES", 2000)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=1, all_changes=all_changes, duration_s=10), None)
    cap.started = 8
    set_clock_origin(cap, 1_000_000)

    def update(ms, value, arrival):
        clock[0] = arrival
        feed(cap, "/a", value, "string", t_us=1_000_000 + ms * 1000)

    update(100, "A", 10.1)
    update(200, "B", 11.1)
    update(300, "x" * 10_000, 11.2)
    update(400, "C", 11.3)
    result = ask(cap, ["/a"], "durations")
    assert result["dropped"] == 1
    assert result["data"]["/a"]["complete_runs"] == 0
    assert result["data"]["/a"]["drop_affected"]
    assert buffer_stats(cap).coalesced == 0
    raw = ask(cap, ["/a"], "samples")["data"]["/a"]
    assert raw["v"] == ["A", "B", "C"] and raw["drop_affected"]
    update(500, "D", 12.1)
    update(600, "E", 13.1)
    result = ask(cap, ["/a"], "durations")["data"]["/a"]
    assert result["states"] == {"D": {"runs": 1, "mean_ms": 100, "min_ms": 100, "max_ms": 100}}
    monkeypatch.setattr(capture_buffer, "PER_TOPIC_CAP", 3)
    update(700, "F", 14.1)
    update(800, "G", 15.1)
    assert drop_barrier_position(cap, "/a") == 0
    assert ask(cap, ["/a"], "durations")["data"]["/a"]["drop_affected"]


def test_delayed_samples_cannot_cross_oversized_drop_frontier(monkeypatch):
    monkeypatch.setattr(capture_buffer, "MAX_BUFFER_BYTES", 2000)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 3
    set_clock_origin(cap, 1_000_000)

    def update(ms, value):
        feed(cap, "/a", value, "string", t_us=1_000_000 + ms * 1000)

    update(300, "x" * 10_000)
    update(50, "A")
    update(100, "B")
    update(400, "C")
    result = ask(cap, ["/a"], "durations")["data"]["/a"]
    assert result["complete_runs"] == 0 and result["drop_affected"]
    update(500, "D")
    update(600, "E")
    result = ask(cap, ["/a"], "durations")["data"]["/a"]
    assert result["states"] == {"D": {"runs": 1, "mean_ms": 100, "min_ms": 100, "max_ms": 100}}


def test_delayed_oversized_update_keeps_prior_timestamp_frontier(monkeypatch):
    monkeypatch.setattr(capture_buffer, "MAX_BUFFER_BYTES", 2000)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 3
    set_clock_origin(cap, 1_000_000)

    def update(ms, value):
        feed(cap, "/a", value, "string", t_us=1_000_000 + ms * 1000)

    for ms, value in [(650, "c"), (2000, "z"), (300, "x" * 10_000),
                      (500, "a"), (600, "b"), (700, "c")]:
        update(ms, value)
    result = ask(cap, ["/a"], "durations")["data"]["/a"]
    assert result.get("complete_runs", 0) == 0 and result["drop_affected"]
    update(2100, "a")
    update(2200, "b")
    update(2300, "c")
    result = ask(cap, ["/a"], "durations")["data"]["/a"]
    assert result["states"] == {"b": {"runs": 1, "mean_ms": 100, "min_ms": 100, "max_ms": 100}}


def test_oversized_drop_preserves_both_sides_without_bridging(monkeypatch):
    monkeypatch.setattr(capture_buffer, "MAX_BUFFER_BYTES", 2000)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 3
    set_clock_origin(cap, 1_000_000)

    def update(ms, value):
        feed(cap, "/a", value, "boolean", t_us=1_000_000 + ms * 1000)

    for ms, value in [(100, False), (200, True), (300, False), (400, "x" * 10_000),
                      (500, True), (600, False), (700, True)]:
        update(ms, value)

    def result(reduction, since=None, until=None):
        return ask(cap, ["/a"], reduction, since_ms=since, until_ms=until)["data"]["/a"]

    earlier = result("edges", until=350)
    assert earlier["rising_ms"] == [200] and earlier["falling_ms"] == [300]
    assert earlier["drop_affected"] and not earlier.get("gap_affected")
    changes = result("changes")
    assert changes["t_ms"] == [100, 200, 300, 500, 600, 700]
    assert changes["drop_affected"] and not changes.get("gap_affected")
    edges = result("edges")
    assert edges["rising_ms"] == [200, 700] and edges["falling_ms"] == [300, 600]
    durations = result("durations")
    assert durations["complete_runs"] == 2
    weighted = result("time_weighted")
    assert weighted["covered_ms"] == 400 and weighted["mean"] == 0.5
    earlier_weighted = result("time_weighted", until=350)
    assert earlier_weighted["covered_ms"] == 200 and earlier_weighted["mean"] == 0.5
    clipped = result("time_weighted", since=250, until=650)
    assert clipped["covered_ms"] == 200 and clipped["mean"] == 0.75
    summary = result("summary")
    assert summary["first"] is False and summary["last"] is True
    assert summary["mean"] == 0.5 and summary["drop_affected"]

    update(800, "x" * 10_000)
    update(900, True)
    update(1000, False)
    assert result("edges", until=350)["rising_ms"] == [200]
    assert result("edges")["falling_ms"] == [300, 600, 1000]  # clean data between two drops survives
    monkeypatch.setattr(capture_buffer, "PER_TOPIC_CAP", 3)
    for ms, value in [(1100, True), (1200, False), (1300, True),
                      (1400, False), (1500, True), (1600, False)]:
        update(ms, value)
    assert drop_barrier_position(cap, "/a") == 0
    assert result("edges")["rising_ms"] == [1100, 1300, 1500]


def test_delayed_drop_and_late_samples_invalidate_only_affected_history(monkeypatch):
    monkeypatch.setattr(capture_buffer, "MAX_BUFFER_BYTES", 2000)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 3
    set_clock_origin(cap, 1_000_000)

    def update(ms, value):
        feed(cap, "/a", value, "boolean", t_us=1_000_000 + ms * 1000)

    for ms, value in [(100, False), (200, True), (300, False), (250, "x" * 10_000),
                      (400, False), (500, True)]:
        update(ms, value)
    before = ask(cap, ["/a"], "edges", until_ms=225)["data"]["/a"]
    assert before["rising_ms"] == [200] and before["falling_ms"] == []
    across = ask(cap, ["/a"], "edges")["data"]["/a"]
    assert across["rising_ms"] == [200, 500] and across["falling_ms"] == []
    update(150, False)
    update(600, False)
    update(700, True)
    late = ask(cap, ["/a"], "edges")["data"]["/a"]
    assert late["rising_ms"] == [700] and late["falling_ms"] == []
    assert late["order_affected"] and late["drop_affected"]
    weighted = ask(cap, ["/a"], "time_weighted")["data"]["/a"]
    assert weighted["covered_ms"] == 150
    assert weighted["out_of_order_updates"] == 1 and weighted["drop_affected"]


def test_timestamp_frontier_survives_eviction_of_unplaceable_sample(monkeypatch):
    local_clock = [1_300_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: local_clock[0])
    monkeypatch.setattr(capture_buffer, "PER_TOPIC_CAP", 2)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 3
    set_clock_origin(cap, 1_000_000)

    def update(ts, value):
        if 1_000_000 <= ts <= 4_000_000:
            local_clock[0] = max(local_clock[0], ts + 1000)
        feed(cap, "/a", value, "boolean", t_us=ts)

    update(0, True)
    update(1_050_000, False)
    update(1_100_000, True)
    update(1_400_000, False)
    result = ask(cap, ["/a"], "edges")["data"]["/a"]
    assert result["rising_ms"] == [] and result["falling_ms"] == []
    assert result["timestamp_affected"] and result["unplaceable_updates"] == 1
    update(1_500_000, True)
    result = ask(cap, ["/a"], "edges")["data"]["/a"]
    assert result["rising_ms"] == [500]


def test_coalescing_is_counted_and_keeps_latest():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=1e-15, all_changes=False, duration_s=10), None)
    set_clock_origin(cap, ntcore._now())
    feed(cap, "/a", "first", "string")
    feed(cap, "/a", "second", "string")
    result = query(cap)
    assert result["coalesced"] == 1
    assert result["data"]["/a"]["v"] == ["second"]


def test_coalescing_preserves_order_regression_for_timing_reductions(monkeypatch):
    clock = [10.1]
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=1, all_changes=False, duration_s=10), None)
    cap.started = 8
    set_clock_origin(cap, 1_000_000)

    def update(ms, value, arrival):
        clock[0] = arrival
        feed(cap, "/a", value, "double", t_us=1_000_000 + ms * 1000)

    update(0, 0, 10.1)
    update(1000, 10, 11.1)
    update(500, 100, 11.2)
    update(2000, 0, 11.3)
    update(2500, 2, 11.4)
    assert retained_times(cap, "/a") == [0, 1000, 500, 2000, 2500]
    result = ask(cap, ["/a"], "time_weighted")["data"]["/a"]
    assert result["out_of_order_updates"] == 1
    assert result["covered_ms"] == 1000 and result["mean"] == 0
    assert buffer_stats(cap).coalesced == 0
    update(2600, 3, 12.1)
    update(2700, 4, 12.2)
    assert buffer_stats(cap).coalesced == 1
    update(2200, 5, 13.1)
    update(2800, 6, 13.2)
    assert retained_times(cap, "/a")[-3:] == [2700, 2200, 2800]
    assert buffer_stats(cap).coalesced == 1


def test_coalescing_does_not_replace_usable_samples_across_gaps(monkeypatch):
    clock = [10.1]
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=1, all_changes=False, duration_s=10), None)
    cap.started = 8
    set_clock_origin(cap, 1_000_000)
    add_gap(cap, Gap(200, 300))

    def update(ms, value):
        feed(cap, "/a", value, "double", t_us=1_000_000 + ms * 1000)

    update(100, 1)
    update(250, 2)
    result = ask(cap, ["/a"], "latest")["data"]["/a"]
    assert result["v"] == 1 and result["used_samples"] == 1 and result["gap_affected"]
    update(400, 3)
    update(500, 4)
    assert retained_times(cap, "/a") == [100, 250, 500]
    result = ask(cap, ["/a"], "samples")["data"]["/a"]
    assert result["v"] == [1, 4] and result["gap_affected"]
    assert buffer_stats(cap).coalesced == 1


@pytest.mark.parametrize("all_changes", [False, True])
@pytest.mark.parametrize("unplaceable_time", [0, 9_000_000])
def test_unplaceable_samples_do_not_erase_or_time_reliable_updates(monkeypatch, all_changes, unplaceable_time):
    clock = [10.1]
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    local_clock = [1_700_000]
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: local_clock[0])
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=1, all_changes=all_changes, duration_s=10), None)
    cap.started = 8
    set_clock_origin(cap, 1_000_000)
    add_gap(cap, Gap(200, 300))

    def update(ts, value, arrival):
        clock[0] = arrival
        if 1_000_000 <= ts <= 4_000_000:
            local_clock[0] = max(local_clock[0], ts + 1000)
        feed(cap, "/a", value, "boolean", t_us=ts)

    update(1_400_000, False, 10.1)
    update(unplaceable_time, True, 10.2)
    assert retained_count(cap, "/a") == 2 and buffer_stats(cap).coalesced == 0
    latest = ask(cap, ["/a"], "latest", since_ms=350)["data"]["/a"]
    assert latest["v"] is False and latest["timestamp_affected"] and latest["unplaceable_updates"] == 1
    edges = ask(cap, ["/a"], "edges", since_ms=350)["data"]["/a"]
    assert not edges.get("rising_ms") and edges["timestamp_affected"]
    summary = ask(cap, ["/a"], "summary", since_ms=350)
    assert "constants" not in summary and summary["data"]["/a"]["timestamp_affected"]
    update(1_800_000, False, 11.1)
    update(1_900_000, True, 12.1)
    edges = ask(cap, ["/a"], "edges", since_ms=350)["data"]["/a"]
    assert edges["rising_ms"] == [900] and edges["timestamp_affected"]


def test_capture_evidence_reports_per_topic_retention_and_drops(monkeypatch):
    cap = Capture("c", CaptureSpec(topics=["/a", "/b", "/c"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    sample = "x" * 100
    monkeypatch.setattr(capture_buffer, "MAX_BUFFER_BYTES", capture_types.sample_size(sample) * 2)
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: 10_000_000)
    for name in ("/a", "/b", "/c"):
        feed(cap, name, sample, "string")
    set_last_update_time(cap, "/b", (9_000_000, 9_000_000))
    set_instance(cap, SimpleNamespace(getServerTimeOffset=lambda: 0))
    result = ask(cap, ["/a", "/b", "/c"], "latest", include_evidence=True)
    assert result["evidence"]["/a"]["dropped"] == 1
    assert result["evidence"]["/a"]["retained_ms"] is None
    assert result["evidence"]["/b"]["retained_samples"] == 1
    assert result["evidence"]["/b"]["last_update"] == {"age_ms": 1000.0}
    assert result["evidence"]["/c"]["last_update"]["age_ms"] is None
    set_instance(cap, None)


def test_capture_evidence_counts_coalescing_separately():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=1e-15, all_changes=False, duration_s=10), None)
    set_clock_origin(cap, ntcore._now())
    feed(cap, "/a", "first", "string")
    feed(cap, "/a", "second", "string")
    evidence = ask(cap, ["/a"], "latest", include_evidence=True)["evidence"]["/a"]
    assert evidence["coalesced"] == 1 and evidence["dropped"] == 0


def test_log_stops_at_byte_limit_and_reports_truncation(tmp_path, monkeypatch):
    path = tmp_path / "capture.wpilog"
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), path)
    attach_log(cap, path.open("xb"))
    monkeypatch.setattr(capture_log, "MAX_LOG_BYTES", 400)
    for _ in range(10):
        feed(cap, "/a", "x" * 100, "string")
    result = query(cap)
    assert result["log_truncated"] is True
    assert result["log_bytes"] == path.stat().st_size <= 400
    assert path.read_bytes().startswith(b"WPILOG")  # what was written before the cap is a valid log


def test_log_write_failure_is_exposed():
    class BrokenLog:
        def write(self, _data):
            raise OSError("disk full")

        def close(self):
            pass

    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.log_path = "test.wpilog"
    attach_log(cap, BrokenLog())
    feed(cap, "/a", "value", "string")
    assert query(cap)["log_error"] == "disk full"
    assert log_file(cap) is None


def test_duplicate_topics_are_subscribed_once():
    assert Capture("c", CaptureSpec(topics=["/a", "/a", "/b"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None).topics == ["/a", "/b"]


def test_topic_reports_when_its_oldest_samples_were_evicted(monkeypatch):
    monkeypatch.setattr(capture_buffer, "PER_TOPIC_CAP", 3)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    set_clock_origin(cap, 1_000_000)
    for i in range(5):
        ts = 1_000_000 + (i + 1) * 100_000
        monkeypatch.setattr(nt_clock, "local_now_us", lambda ts=ts: ts)
        feed(cap, "/a", float(i), "double", t_us=ts)
    result = ask(cap, ["/a"], "summary")["data"]["/a"]
    assert result["n"] == 3 and result["evicted_oldest"] == 2


def test_an_update_that_cannot_be_dated_is_left_out_of_the_log_and_counted(tmp_path, monkeypatch):
    from frc_mcp_nt4 import clock as nt_clock

    path = tmp_path / "capture.wpilog"
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), path)
    attach_log(cap, path.open("xb"))
    feed(cap, "/a", 1.0)  # placed on the capture's timeline
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: None)  # now there is no local clock to date an arrival by
    feed(cap, "/a", 2.0)
    feed(cap, "/a", 3.0)
    result = ask(cap, ["/a"], "latest")
    assert result["dropped_nonfinite_time"] == 2 and result["log_bytes"] > 0
    cap._logfile.close()
    reader = DataLogReader(str(path))
    values = [rec.getDouble() for rec in reader if not rec.isControl()]
    assert values == [1.0]  # not two more at time 0

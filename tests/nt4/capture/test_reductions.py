"""Deterministic tests for capture reductions (edges, durations, time-weighted, summaries)."""

import time

import pytest
from capture_helpers import add_gap, ask, feed, seed_samples, set_clock_origin

from frc_mcp_nt4 import clock as nt_clock
from frc_mcp_nt4.capture import Capture, CaptureSpec
from frc_mcp_wpilib import conditions
from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilib.reduce import stats as capture_stats
from frc_mcp_wpilib.reduce.segments import Window
from frc_mcp_wpilib.reduce.stats import Shaper, _durations, _edges, _time_weighted
from frc_mcp_wpilib.reduce.types import Gap


def test_boolean_edges_use_the_update_timestamp_for_sparse_events():
    result = _edges([(0, False), (10_000, True), (12_000, False)], "boolean", None, 20)
    assert result["rising_ms"] == [10_000]
    assert result["falling_ms"] == [12_000]
    assert result["timing"] == "update_timestamp"


def test_numeric_edges_use_the_update_timestamp_not_an_interpolation():
    result = _edges([(0, 0), (1000, 10), (2000, 0)], "double", 5, 20)
    assert result["rising_ms"] == [1000]
    assert result["falling_ms"] == [2000]
    assert result["timing"] == "update_timestamp"


def test_numeric_step_after_a_long_hold_is_not_dated_inside_the_hold():
    result = _edges([(0, 5.0), (1642, 42.5), (2049, 5.0), (6639, 42.5)], "double", 20, 20)
    assert result["rising_ms"] == [1642, 6639] and result["falling_ms"] == [2049]


def test_edge_limits_and_invalid_inputs():
    result = _edges([(0, False), (100, True), (200, False), (300, True)], "boolean", None, 1)
    assert result["rising_ms"] == [100]
    assert result["falling_ms"] == [200]
    assert result["truncated"] == {"rising_ms": 2, "falling_ms": 1}
    with pytest.raises(WPILibMCPError, match="threshold"):
        _edges([(0, 0), (100, 1)], "double", None, 1)
    with pytest.raises(WPILibMCPError, match="numeric or boolean"):
        _edges([(0, "a"), (100, "b")], "string", 1, 1)


@pytest.mark.parametrize("type_str, values", [
    ("double", [0.0, 10.0, 0.0, 10.0]),
    ("boolean", [False, True, False, True]),
])
def test_edges_never_infer_a_crossing_over_a_gap(type_str, values):
    rows = list(zip([0, 100, 300, 400], values, strict=True))
    result = _edges(rows, type_str, 5 if type_str == "double" else None, 20, Window(gaps=[[150, 250]]))
    assert len(result["rising_ms"]) == 2 and result["falling_ms"] == []
    assert result["gap_affected"] and result["used_samples"] == 4
    assert not any(150 <= t <= 250 for t in result["rising_ms"])


def test_reverse_order_samples_cannot_bridge_a_gap():
    rows = [(100, 0), (1100, 0), (200, 10), (1200, 0)]
    gaps = [[500, 1000]]
    edges = _edges(rows, "double", 5, 20, Window(gaps=gaps))
    assert edges["rising_ms"] == [] and edges["falling_ms"] == []
    assert edges["gap_affected"] and edges["order_affected"] and edges["used_samples"] == 2
    changes = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "changes", 20, window=Window(gaps=gaps))
    assert changes["v"] == [0, 0] and changes["gap_affected"] and changes["order_affected"]
    durations = _durations(rows, lambda v: v, Window(gaps=gaps))
    assert durations["complete_runs"] == 0 and durations["gap_affected"]


def test_edges_and_durations_exclude_order_invalidated_transitions():
    rows = [(0, 0), (100, 10), (200, 0), (50, 0), (300, 10), (400, 0), (500, 10)]
    edges = _edges(rows, "double", 5, 20)
    assert edges["rising_ms"] == [500] and edges["falling_ms"] == [400]
    assert edges["order_affected"] and edges["out_of_order_updates"] == 1
    durations = _durations(rows, lambda v: v)
    assert durations["complete_runs"] == 1
    assert durations["states"]["0"]["mean_ms"] == 100
    assert durations["order_affected"] and durations["used_samples"] == 4


def test_windowed_edges_cannot_hide_delayed_updates():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 2
    seed_samples(cap, "/a", [(0, 0, True), (200, 10, True), (100, 10, True), (300, 0, True), (400, 10, True)], "double")
    result = ask(cap, ["/a"], "edges", since_ms=150, threshold=5)["data"]["/a"]
    assert result["rising_ms"] == [400] and result["falling_ms"] == []
    assert result["order_affected"] and result["out_of_order_updates"] == 1


def test_changes_and_summary_do_not_infer_arrival_order_as_time_order():
    rows = [(0, False), (100, True), (50, True), (200, False)]
    changes = capture_stats.reduce_rows(rows, Shaper("boolean", None, 4), "changes", 20)
    assert changes["t_ms"] == [0, 200] and changes["v"] == [False, False]
    assert changes["order_affected"] and changes["out_of_order_updates"] == 1
    summary = capture_stats.reduce_rows(rows, Shaper("boolean", None, 4), "summary", 20)
    assert summary["const"] is False and summary["order_affected"]
    assert summary["used_samples"] == 2


@pytest.mark.parametrize("reduction", ["changes", "summary"])
def test_windowed_changes_and_summary_account_for_delayed_history(reduction):
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 2
    seed_samples(cap, "/a", [(0, "a", True), (200, "b", True), (100, "b", True), (300, "c", True), (400, "d", True)], "string")
    response = ask(cap, ["/a"], reduction, since_ms=150)
    result = response["data"]["/a"]
    assert result["order_affected"] and result["used_samples"] == 2
    if reduction == "changes":
        assert result["t_ms"] == [300, 400] and result["v"] == ["c", "d"]
    else:
        assert result["first"] == "c" and result["last"] == "d" and result["changes"] == 1
        assert "capture_rate_hz" not in result and "constants" not in response


def test_summary_does_not_count_changes_across_gaps_or_delayed_samples():
    rows = [(0, "a"), (100, "b"), (300, "c"), (250, "d"), (400, "e"), (500, "f")]
    result = capture_stats.reduce_rows(rows, Shaper("string", None, 4), "summary", 20, window=Window(gaps=[[150, 200]]))
    assert result["first"] == "a" and result["last"] == "f"
    assert result["changes"] == 2
    assert result["gap_affected"] and result["order_affected"]


def test_durations_exclude_partial_runs_on_both_sides_of_gap():
    rows = [(0, "a"), (10, "b"), (20, "c"), (30, "d"),
            (100, "d"), (110, "e"), (120, "f"), (130, "g")]
    result = _durations(rows, lambda v: v, Window(gaps=[[40, 90]]))
    assert result["complete_runs"] == 4
    assert set(result["states"]) == {"b", "c", "e", "f"}
    assert all(state["mean_ms"] == 10 for state in result["states"].values())
    assert result["gap_affected"]


def test_changes_reducer_treats_repeated_nan_as_unchanged():
    rows = [(0, float("nan")), (1, float("nan")), (2, 1.0), (3, float("nan"))]
    result = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "changes", 20)
    assert result["n"] == 3 and result["t_ms"] == [0, 2, 3]
    result = capture_stats.reduce_rows(rows[:2], Shaper("double", None, 4), "summary", 20)
    assert result["non_finite"] == 2 and "No finite" in result["note"] and "const" not in result  # not a "constant" NaN


def test_duration_reducer_treats_consecutive_nan_values_as_one_run():
    rows = [(0, 0.0), (10, float("nan")), (20, float("nan")), (30, float("nan")), (40, 1.0)]
    result = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "durations", 20)
    assert result["runs_seen"] == 3 and result["complete_runs"] == 1
    assert result["states"]["nan"] == {"runs": 1, "mean_ms": 30, "min_ms": 30, "max_ms": 30}


def test_time_weighted_mean_uses_held_values_not_sample_average():
    rows = [(0, 0), (1000, 10), (3000, 0)]
    result = _time_weighted(rows, "double", 4)
    assert result["mean"] == pytest.approx(20 / 3, abs=0.0001)
    assert result["covered_ms"] == 3000 and result["method"] == "previous_value_hold"
    huge = _time_weighted([(0, 1e308), (1000, 1e308), (2000, 0)], "double", 4)
    assert huge["mean"] == pytest.approx(1e308)


def test_time_weighted_mean_excludes_gaps_and_unordered_intervals():
    rows = [(0, 0), (1000, 10), (3000, 20), (3000, 30), (4000, 0)]
    result = _time_weighted(rows, "double", 4, Window(gaps=[[1200, 2000]]))
    assert result["gap_affected"] and result["covered_ms"] == 2000
    assert result["mean"] == 15 and result["skipped_nonincreasing_intervals"] == 1
    short = _time_weighted([(0, 1)], "int", 4)
    assert short["covered_ms"] == 0 and "record longer" in short["note"]
    with pytest.raises(WPILibMCPError, match="numeric or boolean"):
        _time_weighted([(0, "a"), (1, "b")], "string", 4)


def test_time_weighted_invalidates_overlaps_and_resumes_after_new_baseline():
    original = _time_weighted([(0, 0), (2000, 10), (1000, 100), (3000, 0)], "double", 4)
    assert original["covered_ms"] == 1000 and original["mean"] == 0
    rows = [(0, 0), (2000, 10), (1000, 100), (3000, 20), (4000, 0)]
    result = _time_weighted(rows, "double", 4)
    assert result["covered_ms"] == 2000
    assert result["mean"] == 10
    assert result["skipped_nonincreasing_intervals"] == 1
    assert result["out_of_order_updates"] == 1
    assert result["skipped_ambiguous_intervals"] == 1


def test_time_weighted_window_cannot_hide_delayed_updates():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 5
    seed_samples(cap, "/a", [(0, 0, True), (2000, 10, True), (1000, 100, True), (3000, 0, True)], "double")
    result = ask(cap, ["/a"], "time_weighted", since_ms=1500)["data"]["/a"]
    assert result["n"] == 2 and result["covered_ms"] == 0
    assert "mean" not in result and result["out_of_order_updates"] == 1
    result = ask(cap, ["/a"], "time_weighted", until_ms=2500)["data"]["/a"]
    assert result["covered_ms"] == 1000 and result["mean"] == 0
    assert result["out_of_order_updates"] == 1


def test_time_weighted_applies_window_after_ordering_clips_coverage():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 5
    seed_samples(cap, "/a", [(0, 5, True), (100, 1, True), (2000, 9, True), (1000, 8, True), (3000, 3, True)], "double")
    result = ask(cap, ["/a"], "time_weighted", until_ms=1500)["data"]["/a"]
    assert result["covered_ms"] == 1000 and result["mean"] == 1.4
    result = ask(cap, ["/a"], "time_weighted", since_ms=100, until_ms=1500)["data"]["/a"]
    assert result["covered_ms"] == 900 and result["mean"] == 1
    result = ask(cap, ["/a"], "time_weighted", since_ms=500, until_ms=1500)["data"]["/a"]
    assert result["covered_ms"] == 500 and result["mean"] == 1


def test_time_weighted_clips_observed_intervals_to_query_window():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 3
    seed_samples(cap, "/a", [(0, 0, True), (1000, 10, True), (2000, 0, True)], "double")
    result = ask(cap, ["/a"], "time_weighted", since_ms=500, until_ms=2000)["data"]["/a"]
    assert result["covered_ms"] == 1500 and result["mean"] == pytest.approx(20 / 3, abs=0.0001)
    no_samples = ask(cap, ["/a"], "time_weighted", since_ms=500, until_ms=750)["data"]["/a"]
    assert no_samples["n"] == 0 and no_samples["covered_ms"] == 250 and no_samples["mean"] == 0
    outside = ask(cap, ["/a"], "time_weighted", since_ms=2100, until_ms=2500)["data"]["/a"]
    assert outside["n"] == 0 and outside["covered_ms"] == 0 and "mean" not in outside
    empty = ask(cap, ["/a"], "time_weighted", since_ms=1000, until_ms=1000)["data"]["/a"]
    assert empty["covered_ms"] == 0 and "mean" not in empty


def test_time_weighted_clipped_window_never_crosses_gaps():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    cap.started = time.monotonic() - 2
    add_gap(cap, Gap(150, 350))
    seed_samples(cap, "/a", [(0, 1, True), (100, 2, True), (400, 8, True), (1000, 3, True)], "double")
    result = ask(cap, ["/a"], "time_weighted", since_ms=50, until_ms=700)["data"]["/a"]
    assert result["covered_ms"] == 350 and result["mean"] == 7
    assert result["gap_affected"]


def test_time_weighted_checks_nonfinite_values_only_when_they_contribute():
    duplicate = _time_weighted([(0, float("nan")), (0, 1), (1000, 2)], "double", 4)
    assert duplicate["covered_ms"] == 1000 and duplicate["mean"] == 1
    invalidated = _time_weighted(
        [(0, float("nan")), (1000, 2), (0, 3), (2000, 4), (3000, 5)], "double", 4)
    assert invalidated["covered_ms"] == 1000 and invalidated["mean"] == 4
    outside = _time_weighted([(0, float("nan")), (1000, 2), (2000, 3)], "double", 4, Window(since_ms=1000))
    assert outside["covered_ms"] == 1000 and outside["mean"] == 2
    stale = _time_weighted([(0, float("nan")), (1000, 2), (500, 3)], "double", 4)  # the NaN holds nothing to weigh: left out, counted
    assert stale["non_finite"] == 1


def test_time_weighted_repeated_delays_and_gap_do_not_double_count():
    rows = [(0, 1), (1000, 2), (4000, 3), (5000, 4), (500, 5),
            (4500, 6), (5000, 7), (6000, 8), (7000, 9)]
    result = _time_weighted(rows, "double", 4, Window(gaps=[[2000, 3000]]))
    assert result["covered_ms"] == 1500
    assert result["mean"] == pytest.approx((500 + 8000) / 1500, abs=0.0001)
    assert result["gap_affected"]
    assert result["out_of_order_updates"] == 2
    assert result["skipped_ambiguous_intervals"] == 2


def test_durations_lists_only_the_longest_lived_states():
    rows = [(i * 100.0, i) for i in range(40)] + [(4000.0, 99), (9000.0, 100)]
    result = _durations(rows, lambda raw: raw)
    assert len(result["states"]) == capture_stats.MAX_DURATION_STATES
    assert "99" in result["states"] and result["states_omitted"] == result["complete_runs"] - capture_stats.MAX_DURATION_STATES


def test_struct_summary_with_fields_reports_each_fields_range(monkeypatch):
    monkeypatch.setattr(conditions, "decode_struct_strict", lambda _typ, data: {"x": float(data[0]), "name": "n"})
    monkeypatch.setattr("frc_mcp_wpilib.values.decode_struct_strict",
                        lambda _typ, data: {"x": float(data[0]), "name": "n"})
    cap = Capture("c", CaptureSpec(topics=["/s"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None)
    set_clock_origin(cap, 1_000_000)
    for i, x in enumerate((2, 8, 5)):
        ts = 1_000_000 + (i + 1) * 100_000
        monkeypatch.setattr(nt_clock, "local_now_us", lambda ts=ts: ts)
        feed(cap, "/s", bytes([x]), "struct:P", t_us=ts)
    result = ask(cap, ["/s"], "summary", fields=["x"])["data"]["/s"]
    assert result["fields"] == {"x": {"min": 2.0, "max": 8.0, "mean": 5.0}}


# --- percentiles and rate ----------------------------------------------------
def test_percentiles_show_the_spread_a_mean_hides():
    rows = [(i * 20.0, float(v)) for i, v in enumerate(range(1, 101))]  # 1..100 every 20 ms
    result = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "percentiles", 20)
    assert result == {"n": 100, "min": 1.0, "p50": 50.5, "p95": 95.05, "p99": 99.01, "max": 100.0}


def test_percentiles_of_one_sample_are_that_sample():
    result = capture_stats.reduce_rows([(0.0, 7.0)], Shaper("double", None, 4), "percentiles", 20)
    assert result["min"] == result["p50"] == result["p99"] == result["max"] == 7.0


@pytest.mark.parametrize("reduction", ["percentiles", "rate"])
def test_percentiles_and_rate_need_a_number_topic(reduction):
    with pytest.raises(WPILibMCPError, match="needs a number topic"):
        capture_stats.reduce_rows([(0.0, "a"), (10.0, "b")], Shaper("string", None, 4), reduction, 20)
    with pytest.raises(WPILibMCPError, match="needs a number topic"):
        capture_stats.reduce_rows([(0.0, True), (10.0, False)], Shaper("boolean", None, 4), reduction, 20)


def test_rate_reports_units_per_second_with_the_fastest_rise_and_fall():
    rows = [(0.0, 0.0), (1000.0, 10.0), (2000.0, 10.0), (2500.0, 0.0)]  # +10/s, 0/s, -20/s
    result = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "rate", 20)
    assert result["max_rise_per_s"] == 10.0 and result["max_rise_at_ms"] == 1000.0
    assert result["max_fall_per_s"] == -20.0 and result["max_fall_at_ms"] == 2500.0
    assert result["mean_abs_per_s"] == 10.0  # (10 + 0 + 20) / 3


def test_rate_leaves_out_a_direction_that_never_happened():
    rows = [(0.0, 0.0), (1000.0, 5.0), (2000.0, 10.0)]
    result = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "rate", 20)
    assert result["max_rise_per_s"] == 5.0 and "max_fall_per_s" not in result


def test_rate_needs_two_samples_at_different_times():
    for rows in ([(0.0, 1.0)], [(5.0, 1.0), (5.0, 2.0)]):
        result = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "rate", 20)
        assert "note" in result and "mean_abs_per_s" not in result


def test_rate_does_not_invent_a_slope_across_an_outage():
    rows = [(0.0, 0.0), (100.0, 1.0), (5000.0, 100.0), (5100.0, 101.0)]  # the 100 jump happens during the outage
    result = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "rate", 20, window=Window(gaps=[[1000.0, 4000.0]]))
    assert result["max_rise_per_s"] == 10.0 and result["gap_affected"]


def _fake_struct(monkeypatch):
    """Rows are (ms, x); the struct decoder is replaced so the tests need no schema."""
    def shape(raw, _type, fields, _digits):
        value = {"x": raw, "name": "n"}
        return {key: leaf for key, leaf in value.items() if key in fields} if fields else value

    monkeypatch.setattr(capture_stats, "shape", shape)


def test_percentiles_and_rate_work_on_a_struct_field(monkeypatch):
    _fake_struct(monkeypatch)
    rows = [(0.0, 0.0), (1000.0, 10.0), (2000.0, 0.0)]
    spread = capture_stats.reduce_rows(rows, Shaper("struct:P", ["x"], 4), "percentiles", 20)
    assert spread == {"type": "struct:P", "n": 3, "fields": {"x": {"min": 0.0, "p50": 0.0, "p95": 9.0, "p99": 9.8, "max": 10.0}}}
    speed = capture_stats.reduce_rows(rows, Shaper("struct:P", ["x"], 4), "rate", 20)
    assert speed["fields"]["x"] == {"mean_abs_per_s": 10.0, "max_rise_per_s": 10.0, "max_rise_at_ms": 1000.0,
                                    "max_fall_per_s": -10.0, "max_fall_at_ms": 2000.0}


@pytest.mark.parametrize("reduction", ["percentiles", "rate"])
def test_a_struct_needs_fields_for_percentiles_and_rate(monkeypatch, reduction):
    _fake_struct(monkeypatch)
    rows = [(0.0, 1.0), (10.0, 2.0)]
    with pytest.raises(WPILibMCPError, match="needs `fields`"):
        capture_stats.reduce_rows(rows, Shaper("struct:P", None, 4), reduction, 20)
    with pytest.raises(WPILibMCPError, match="none of the fields"):
        capture_stats.reduce_rows(rows, Shaper("struct:P", ["name"], 4), reduction, 20)


# --- step_response -----------------------------------------------------------
def _second_order(zeta, steps=300, dt_ms=20.0, low=10.0, high=90.0):
    """A textbook step response (natural frequency 10 rad/s) from `low` to `high`, sampled every `dt_ms`."""
    import math
    wn = 10.0
    wd = wn * math.sqrt(1 - zeta**2)
    rows = []
    for i in range(steps):
        t = i * dt_ms / 1000
        y = 1 - math.exp(-zeta * wn * t) * (math.cos(wd * t) + zeta / math.sqrt(1 - zeta**2) * math.sin(wd * t))
        rows.append((i * dt_ms, low + (high - low) * y))
    return rows


def _step(rows, **kwargs):
    return capture_stats.reduce_rows(rows, Shaper("double", None, 4), "step_response", 20, **kwargs)


def test_step_response_of_an_underdamped_loop_matches_the_textbook_numbers():
    result = _step(_second_order(0.5))
    assert (result["start"], result["final"]) == (10.0, 90.0)
    assert result["overshoot_pct"] == 16.3  # exp(-pi * zeta / sqrt(1 - zeta^2))
    assert result["peak_at_ms"] == 360.0 and result["peak"] > 103
    assert result["rise_ms"] == 160.0
    assert result["settle_ms"] == pytest.approx(800, abs=40)  # about 4 / (zeta * wn) seconds


def test_step_response_without_overshoot_has_no_peak():
    result = _step([(i * 50.0, 90.0 * (1 - 0.8**i)) for i in range(60)])  # a first-order response never overshoots
    assert result["overshoot_pct"] == 0.0 and "peak" not in result and "peak_at_ms" not in result
    assert result["rise_ms"] == 500.0 and result["settle_ms"] == 900.0


def test_step_response_works_for_a_falling_step():
    rows = [(t, 100.0 - value) for t, value in _second_order(0.5, low=0.0, high=100.0)]
    result = _step(rows)
    assert (result["start"], result["final"]) == (100.0, 0.0)
    assert result["overshoot_pct"] == 16.3 and result["rise_ms"] == 160.0


def test_step_response_says_when_there_was_no_step():
    assert "No step" in _step([(i * 10.0, 5.0) for i in range(10)])["note"]


def test_step_response_says_when_the_value_is_still_moving():
    result = _step([(i * 10.0, float(i)) for i in range(50)])
    assert result["settled"] is False and result["last"] == 49.0 and "note" in result


def test_step_response_needs_a_few_samples():
    assert "at least" in _step([(0.0, 0.0), (10.0, 1.0)])["note"]


def test_step_response_measures_only_the_longest_stretch_between_outages():
    rows = _second_order(0.5)
    shifted = [(t + 10_000.0, v) for t, v in rows[:3]]  # a short stretch after an outage
    result = _step(rows + shifted, window=Window(gaps=[Gap(7_000.0, 9_000.0)]))
    assert result["overshoot_pct"] == 16.3 and result["gap_affected"] is True


def test_step_response_needs_a_number_topic():
    with pytest.raises(WPILibMCPError, match="needs a number topic"):
        capture_stats.reduce_rows([(0.0, "a"), (10.0, "b")], Shaper("string", None, 4), "step_response", 20)


def test_step_response_works_on_a_struct_field(monkeypatch):
    _fake_struct(monkeypatch)
    rows = [(t, v) for t, v in _second_order(0.5)]
    result = capture_stats.reduce_rows(rows, Shaper("struct:P", ["x"], 4), "step_response", 20)
    assert result["fields"]["x"]["overshoot_pct"] == 16.3


# --- NaN and infinity (a disconnected encoder, a divide by zero) -----------------------------------------------
NAN, INF = float("nan"), float("inf")


def _reduce(rows, reduction, type_str="double"):
    return capture_stats.reduce_rows(rows, Shaper(type_str, None, 4), reduction, 20)


def test_summary_leaves_out_non_finite_values_and_counts_them():
    rows = [(i * 10.0, v) for i, v in enumerate([1.0, INF, 3.0, -INF, NAN, 5.0])]  # +inf and -inf together used to raise
    result = _reduce(rows, "summary")
    assert (result["min"], result["max"], result["mean"]) == (1.0, 5.0, 3.0)
    assert result["non_finite"] == 3


def test_summary_of_only_non_finite_values_says_so():
    result = _reduce([(0.0, NAN), (10.0, INF), (20.0, -INF)], "summary")
    assert result["non_finite"] == 3 and "No finite" in result["note"] and "min" not in result


def test_a_clean_summary_has_no_non_finite_key():
    assert "non_finite" not in _reduce([(0.0, 1.0), (10.0, 2.0)], "summary")


def test_percentiles_skip_nan_instead_of_sorting_it_in():
    rows = [(i * 10.0, v) for i, v in enumerate([NAN, 1.0, 2.0, 3.0, 4.0, NAN, 5.0])]
    result = _reduce(rows, "percentiles")
    assert (result["min"], result["p50"], result["max"]) == (1.0, 3.0, 5.0)  # the finite values 1..5
    assert result["non_finite"] == 2
    assert "No finite" in _reduce([(0.0, NAN), (10.0, NAN)], "percentiles")["note"]


def test_rate_skips_the_slopes_next_to_a_non_finite_value():
    rows = [(0.0, 0.0), (1000.0, 10.0), (2000.0, NAN), (3000.0, 30.0), (4000.0, 20.0)]
    result = _reduce(rows, "rate")
    assert result["max_rise_per_s"] == 10.0 and result["max_fall_per_s"] == -10.0
    assert result["non_finite"] == 1  # one NaN sample (its two slopes are left out), counted as samples like summary does
    assert _reduce(rows, "summary")["non_finite"] == result["non_finite"]


def test_step_response_with_nan_in_the_tail_does_not_crash():
    base = [(i * 50.0, 90.0 * (1 - 0.8**i)) for i in range(60)]
    clean = _step(base)
    nan_tail = _step([*base[:-1], (base[-1][0], NAN)])  # used to raise a bare StopIteration inside the tool
    assert nan_tail["non_finite"] == 1
    assert nan_tail["start"] == clean["start"] and nan_tail["rise_ms"] == clean["rise_ms"]
    assert "non_finite" not in clean
    assert "at least" in _step([(0.0, NAN), (10.0, NAN), (20.0, INF), (30.0, 1.0), (40.0, 2.0)])["note"]


def test_edges_compare_across_a_non_finite_sample():
    rows = [(0.0, 0.0), (10.0, NAN), (20.0, 1.0), (30.0, NAN), (40.0, 0.0)]
    result_at = capture_stats.reduce_rows(rows, Shaper("double", None, 4), "edges", 20, threshold=0.5)
    assert result_at["rising_ms"] == [20.0] and result_at["falling_ms"] == [40.0] and result_at["non_finite"] == 2
    assert "non_finite" not in capture_stats.reduce_rows([(0.0, 0.0), (10.0, 1.0)], Shaper("double", None, 4), "edges", 20, threshold=0.5)


def test_nothing_finite_is_one_clear_note_in_every_reduction():
    nans = [(i * 10.0, NAN) for i in range(8)]
    for reduction in ("summary", "percentiles", "rate", "step_response", "time_weighted"):
        result = _reduce(nans, reduction)
        assert result["non_finite"] == 8, reduction
        assert "No finite values" in result["note"] and "record longer" not in result["note"], reduction
        assert "const" not in result and "mean" not in result, reduction


def test_a_rate_that_overflows_a_float_is_not_called_a_short_log():
    result = _reduce([(0.0, -1e308), (1.0, 1e308)], "rate")
    assert "overflows" in result["note"] and "record longer" not in result["note"] and "non_finite" not in result


def test_time_weighted_leaves_out_the_intervals_a_non_finite_value_holds():
    rows = [(0.0, 2.0), (1000.0, NAN), (2000.0, 4.0), (3000.0, 4.0)]
    result = _reduce(rows, "time_weighted")
    assert result["non_finite"] == 1 and result["covered_ms"] == 2000 and result["mean"] == 3.0  # 2.0 for 1 s, 4.0 for 1 s

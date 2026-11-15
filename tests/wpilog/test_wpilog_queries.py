"""The questions asked of an open log: reductions, values at a time, when a condition held, text search."""

import pytest
from dslog_builder import build_session
from log_builder import BASE, S

from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilib.reduce.models import Condition
from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import queries
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.logfile import LogFile, load
from frc_mcp_wpilog.queries import QuerySpec, Range

CURRENT = "/Fake/Elevator/Current"


def ask(log, names, reduce="summary", align="start", since=None, until=None, **spec):
    return queries.query(log, names, QuerySpec(reduce=reduce, **spec), Range(align, since, until))


def test_summary_of_a_number_topic_has_its_unit_and_statistics(sample):
    data = ask(sample, [CURRENT])["data"][CURRENT]
    assert (data["n"], data["min"], data["max"], data["unit"]) == (121, 5.0, 42.5, "A")


def test_edges_date_the_crossings_in_milliseconds_after_time_zero(sample):
    start = ask(sample, [CURRENT], "edges", threshold=40)["data"][CURRENT]
    assert start["rising_ms"] == [3000.0, 7000.0] and start["falling_ms"] == [3500.0, 7200.0]
    auto = ask(sample, [CURRENT], "edges", align="auto", threshold=40)
    assert auto["t0_s"] == 2.0 and auto["data"][CURRENT]["rising_ms"] == [1000.0, 5000.0]


def test_percentiles_and_rate_work_on_a_log(sample):
    spread = ask(sample, [CURRENT], "percentiles")["data"][CURRENT]
    assert (spread["min"], spread["p50"], spread["max"]) == (5.0, 5.0, 42.5)
    speed = ask(sample, ["/Fake/Arm/Angle"], "rate")["data"]["/Fake/Arm/Angle"]
    assert speed["mean_abs_per_s"] == 10.0 and speed["max_rise_per_s"] == 10.0 and "max_fall_per_s" not in speed


def test_a_struct_field_is_reduced_through_the_logs_own_schema(sample):
    data = ask(sample, ["/Fake/Drive/Pose"], "summary", fields=["translation.x"])["data"]["/Fake/Drive/Pose"]
    assert data["type"] == "struct:Pose2d" and data["fields"]["translation.x"] == {"min": 0.0, "max": 3.0, "mean": 1.5}
    spread = ask(sample, ["/Fake/Drive/Pose"], "percentiles", fields=["translation.x"])["data"]["/Fake/Drive/Pose"]
    assert spread["fields"]["translation.x"]["max"] == 3.0


def test_a_window_limits_the_rows_and_unchanging_topics_collapse_into_constants(sample):
    out = ask(sample, [CURRENT, "/Fake/Debug/Raw"], since=2800, until=3200)
    assert out["data"][CURRENT]["n"] == 5  # the rows inside the window only
    assert out["constants"] == {"/Fake/Debug/Raw": 3}  # one update in the window: it never changed


def test_a_topic_unchanged_through_the_window_reports_what_it_held(sample):
    data = ask(sample, ["/Fake/Match/Mode"], "latest", since=2500, until=4500)["data"]["/Fake/Match/Mode"]
    assert data == {"n": 0, "held": "auto", "held_since_ms": 2000.0}


def test_unknown_topics_and_unsuitable_reductions_get_their_own_error(sample):
    reply = ask(sample, ["/nope", "messages", CURRENT], "percentiles")
    data = reply["data"]
    assert data["/nope"] == {"err": queries.NOT_IN_LOG} and reply["hint"] == queries.FIND_NAMES_HINT  # the hint is said once
    assert "needs a number topic" in data["messages"]["err"]
    assert data[CURRENT]["max"] == 42.5  # the others still answer


def test_a_trailing_star_expands_to_the_topics_under_a_prefix_and_respects_the_limit(sample):
    out = ask(sample, ["/Fake/Drive/*", "/Fake/Arm/*"], "latest", max_topics=1)
    assert list(out["data"]) == ["/Fake/Drive/Pose"] and out["omitted_topics"] == 1


def test_values_at_a_time_use_the_last_update_at_or_before_it(sample):
    out = queries.values_at(sample, [CURRENT, "/Fake/Match/Mode", "/nope"], 3200, None, "start")
    assert out[CURRENT] == {"v": 42.5, "t_ms": 3200.0, "unit": "A"}
    assert out["/Fake/Match/Mode"] == {"v": "auto", "t_ms": 2000.0}
    assert out["/nope"] == {"err": f"{queries.NOT_IN_LOG} ({queries.FIND_NAMES_HINT})"}
    two = queries.values_at(sample, ["/nope", "/nada"], None, None, "start")
    assert queries.FIND_NAMES_HINT in two["/nope"]["err"] and two["/nada"] == {"err": queries.NOT_IN_LOG}  # said once per reply
    assert queries.values_at(sample, [CURRENT], None, None, "start")[CURRENT]["t_ms"] == 12000.0  # default: the end
    assert queries.values_at(sample, [CURRENT], 0, None, "auto")[CURRENT]["t_ms"] == 0.0  # at auto start: the 5 A reading


def test_values_before_the_first_update_say_so(sample):
    out = queries.values_at(sample, ["/Fake/Drive/Pose"], 1000, None, "start")
    assert out["/Fake/Drive/Pose"] == {"err": "no value yet at that time"}


def test_struct_values_decode_to_dotted_fields_at_a_time(sample):
    out = queries.values_at(sample, ["/Fake/Drive/Pose"], 3000, ["translation.x"], "start")
    assert out["/Fake/Drive/Pose"]["v"] == {"translation.x": 1.0} and out["/Fake/Drive/Pose"]["type"] == "struct:Pose2d"


def test_when_lists_the_intervals_a_condition_held(sample):
    out = queries.when(sample, CURRENT, ">", 40, None, Range(), 20)
    assert out == {"n": 2, "total_ms": 700.0, "longest_ms": 500.0, "intervals_ms": [[3000.0, 3500.0], [7000.0, 7200.0]]}
    aligned = queries.when(sample, CURRENT, ">", 40, None, Range("auto"), 20)
    assert aligned["intervals_ms"] == [[1000.0, 1500.0], [5000.0, 5200.0]]


def test_when_limits_the_listed_intervals_and_counts_the_rest(sample):
    out = queries.when(sample, CURRENT, ">", 40, None, Range(), 1)
    assert out["n"] == 2 and len(out["intervals_ms"]) == 1 and out["omitted"] == 1


def test_when_a_condition_is_still_true_at_the_end_it_says_so(sample):
    out = queries.when(sample, "/Fake/Arm/Angle", ">=", 115, None, Range(), 20)
    assert out["true_at_end"] is True and out["intervals_ms"] == [[11500.0, 12000.0]] and out["total_ms"] == 500.0


def test_when_a_window_starts_inside_an_interval_the_carried_value_counts(sample):
    out = queries.when(sample, CURRENT, ">", 40, None, Range(since_ms=3200, until_ms=6000), 20)
    assert out["intervals_ms"] == [[3200.0, 3500.0]]


def test_when_works_on_a_struct_field(sample):
    out = queries.when(sample, "/Fake/Drive/Pose", ">=", 2.0, "translation.x", Range(), 20)
    # the last pose update (x = 3 m at 5 s) holds until the log ends, like any value that stops changing
    assert out["intervals_ms"] == [[4000.0, 12000.0]] and out["true_at_end"] is True


def test_when_changed_lists_the_times_the_value_changed(sample):
    out = queries.when(sample, "/Fake/Match/Mode", "changed", None, None, Range(), 20)
    assert out == {"n": 3, "changes_ms": [2000.0, 5000.0, 6000.0]}


def test_when_rejects_unknown_topics_missing_targets_and_bad_fields(sample):
    with pytest.raises(WPILibMCPError, match="not in this log"):
        queries.when(sample, "/nope", ">", 1, None, Range(), 5)
    with pytest.raises(WPILibMCPError, match="needs a `value`"):
        queries.when(sample, CURRENT, ">", None, None, Range(), 5)
    with pytest.raises(WPILibMCPError, match="no field"):
        queries.when(sample, "/Fake/Drive/Pose", ">", 1, "nope", Range(), 5)


def test_text_search_finds_messages_oldest_first_and_skips_blank_values(sample):
    out = queries.search_text(sample, None, None, Range(), 10)
    assert [i["v"] for i in out["items"]] == ["robot program starting", "WARNING: battery low", "ERROR: arm limit switch not found"]
    assert out["searched"] == 1 and out["items"][1]["t_ms"] == 4000.0
    errors = queries.search_text(sample, None, "error", Range("auto"), 10)
    assert errors["n"] == 1 and errors["items"][0]["t_ms"] == 6000.0


def test_text_search_limits_windows_and_reports_unknown_topics(sample):
    out = queries.search_text(sample, ["messages", "nope"], None, Range(since_ms=3000), 1)
    assert out["n"] == 2 and len(out["items"]) == 1 and out["omitted"] == 1 and out["not_in_log"] == ["nope"]


def test_compare_puts_each_topic_side_by_side_and_aligns_each_log_on_its_own(sample, tmp_path):
    other = LogWriter()  # the same kind of run, but the robot clock is 10 s later and the spike comes 2 s into auto
    enabled = other.start("DS:enabled", "boolean", "", 10 * S)
    auto = other.start("DS:autonomous", "boolean", "", 10 * S)
    current = other.start("NT:/Fake/Elevator/Current", "double", "", 10 * S)
    other.append(auto, True, 12 * S)
    other.append(enabled, True, 12 * S)
    other.append(current, 5.0, 12 * S)
    other.append(current, 42.5, 14 * S)
    other.append(current, 5.0, 14 * S + S // 2)
    second = LogFile(other.save(tmp_path / "other.wpilog"))
    out = queries.compare({"l1": sample, "l2": second}, [CURRENT, "/Fake/Arm/Angle"], QuerySpec(reduce="edges", threshold=40), Range("auto"))
    assert out["logs"] == {"l1": "sample.wpilog", "l2": "other.wpilog"}
    assert out["data"][CURRENT]["l1"]["rising_ms"] == [1000.0, 5000.0] and out["data"][CURRENT]["l2"]["rising_ms"] == [2000.0]
    assert out["data"]["/Fake/Arm/Angle"]["l2"] == {"err": queries.NOT_IN_LOG} and out["hint"] == queries.FIND_NAMES_HINT


def test_compare_keeps_unchanging_topics_in_place_instead_of_collapsing_them(sample):
    out = queries.compare({"l1": sample, "l2": sample}, ["/Fake/Match/Mode"], QuerySpec(), Range(since_ms=2500, until_ms=3000))
    assert out["data"]["/Fake/Match/Mode"]["l1"] == {"n": 0, "held": "auto", "held_since_ms": 2000.0}
    assert "constants" not in out


def test_compare_reports_each_logs_time_zero_and_isolates_a_log_that_cannot_be_aligned(sample, tmp_path):
    never_enabled = LogWriter()
    current = never_enabled.start("NT:/Fake/Elevator/Current", "double", "", S)
    never_enabled.append(current, 5.0, S)
    bare = LogFile(never_enabled.save(tmp_path / "bare.wpilog"))
    out = queries.compare({"l1": sample, "l2": bare}, [CURRENT], QuerySpec(), Range("auto"))
    assert out["t0_s"] == {"l1": 2.0}  # where l1's time zero is; l2 has none
    assert "err" not in out["data"][CURRENT]["l1"]
    assert "no such period" in out["data"][CURRENT]["l2"]["err"]
    with pytest.raises(WPILogError, match="l2:"):
        queries.compare({"l2": bare, "l3": bare}, [CURRENT], QuerySpec(), Range("auto"))


def test_step_response_of_a_logged_setpoint_change(tmp_path):
    w = LogWriter()
    entry = w.start("NT:/Arm/Angle", "double", "", BASE)
    for i in range(60):  # a first-order response: 0 -> 90 degrees, one sample every 50 ms
        w.append(entry, 90.0 * (1 - 0.8**i), BASE + int(i * 0.05 * S))
    log = LogFile(w.save(tmp_path / "step.wpilog"))
    result = ask(log, ["/Arm/Angle"], "step_response")["data"]["/Arm/Angle"]
    assert result["start"] == 0.0 and result["overshoot_pct"] == 0.0
    assert result["settle_ms"] == 900.0 and result["rise_ms"] == 500.0


# --- when ... also ----------------------------------------------------------


def when_also(log, also, name=CURRENT, op=">", value=40):
    return queries.when(log, name, op, value, None, Range(), 20, also)


def mode_is(mode):
    return Condition(topic="/Fake/Match/Mode", op="==", value=mode)


def test_also_keeps_only_the_stretches_when_the_other_condition_held_too(sample):
    assert when_also(sample, [])["intervals_ms"] == [[3000.0, 3500.0], [7000.0, 7200.0]]  # both spikes on their own
    assert when_also(sample, [mode_is("auto")])["intervals_ms"] == [[3000.0, 3500.0]]
    assert when_also(sample, [mode_is("teleop")])["intervals_ms"] == [[7000.0, 7200.0]]
    none = when_also(sample, [mode_is("disabled")])
    assert none["n"] == 0 and none["intervals_ms"] == []


def test_also_can_combine_several_topics_and_fields(sample):
    arm_high = Condition(topic="/Fake/Arm/Angle", op=">", value=50)
    both = when_also(sample, [arm_high, mode_is("teleop")])
    assert both["intervals_ms"] == [[7000.0, 7200.0]] and both["total_ms"] == 200.0 and both["longest_ms"] == 200.0
    pose = Condition(topic="/Fake/Drive/Pose", op=">=", value=0, field="translation.x")
    assert when_also(sample, [pose])["n"] == 2


def build_pair(tmp_path, a_changes, b_changes, end=10):
    """A log with two number topics that switch between 0 and 1 at the given seconds, ending `end` seconds in."""
    w = LogWriter()
    for name, changes in (("a", a_changes), ("b", b_changes)):
        entry = w.start(f"NT:/{name}", "double", "", BASE)
        w.append(entry, 0.0, BASE)
        for t, value in changes:
            w.append(entry, value, BASE + int(t * S))
        w.append(entry, changes[-1][1], BASE + end * S)  # each topic repeats its last value at `end`, where the log stops
    return LogFile(w.save(tmp_path / "pair.wpilog"))


def test_also_intervals_that_only_touch_overlap_for_no_time(tmp_path):
    log = build_pair(tmp_path, [(1, 1.0), (2, 0.0)], [(2, 1.0), (3, 0.0)])
    out = queries.when(log, "/a", "==", 1, None, Range(), 20, [Condition(topic="/b", op="==", value=1)])
    assert out["n"] == 0  # a was true until 2 s and b from 2 s: never at once


def test_also_still_true_at_the_end_needs_every_condition_true_there(tmp_path):
    log = build_pair(tmp_path, [(1, 1.0)], [(2, 1.0)], end=5)
    out = queries.when(log, "/a", "==", 1, None, Range(), 20, [Condition(topic="/b", op="==", value=1)])
    assert out["intervals_ms"] == [[2000.0, 5000.0]] and out["true_at_end"] is True


def test_also_rejects_what_it_cannot_answer(sample):
    with pytest.raises(WPILogError, match="/nope"):
        when_also(sample, [Condition(topic="/nope", op="==", value=1)])
    with pytest.raises(WPILogError, match="cannot be combined"):
        when_also(sample, [mode_is("auto")], op="changed", value=None)


def test_a_rate_every_topic_shares_is_said_once_under_all(sample):
    out = ask(sample, [CURRENT, "/Fake/Drive/Pose"])  # both are logged at 10 Hz
    assert out["all"] == {"capture_rate_hz": 10.0}
    assert all("capture_rate_hz" not in v for v in out["data"].values())
    mixed = ask(sample, [CURRENT, "/Fake/Arm/Angle"])  # 10 Hz and 2 Hz: each keeps its own
    assert "all" not in mixed and mixed["data"][CURRENT]["capture_rate_hz"] == 10.0


def test_message_topics_are_found_by_a_keyword_anywhere_in_the_name(tmp_path):
    w = LogWriter()
    for name, text in (("/Robot/Errors/Last", "arm stalled"), ("/Console/Output", "hello"), ("/Arm/Name", "arm")):
        w.append(w.start(name, "string", "", 0), text, S)
    log = LogFile(w.save(tmp_path / "text.wpilog"))
    out = queries.search_text(log, None, None, Range(), 10)
    assert out["searched"] == 2 and {i["topic"] for i in out["items"]} == {"/Robot/Errors/Last", "/Console/Output"}


def test_driver_station_start_time_is_not_taken_for_a_message_topic(tmp_path):
    out = queries.search_text(load(build_session(tmp_path)), None, None, Range(), 10)
    assert out["searched"] == 1 and {i["topic"] for i in out["items"]} == {"/DSEvents"}

"""wpilog_diff: what was set differently between two logs (tunables and constants, never busy signals)."""

import json

import pytest
from fastmcp.exceptions import ToolError
from log_builder import BASE, POSE_SCHEMAS, S, pose_bytes

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import diff_queries, queries
from frc_mcp_wpilog.logfile import LogFile
from frc_mcp_wpilog.tools import data_tools, file_tools


def build(path, *, p, i=0.1, d=0.0, mode=None, d_tuned_to=None, pose=(0.0, 0.0), match=7, extra=None):
    """A log whose settings are given: /Tune/P, /Tune/I, /Tune/D, /Tune/Tuned (1.0, optionally changed once mid-log), a
    struct, a match number, an optional text setting, any extra settings, and a signal that must never be compared."""
    w = LogWriter()
    for name, value in (("/Tune/P", p), ("/Tune/I", i), ("/Tune/D", d)):
        w.append(w.start(f"NT:{name}", "double", "", BASE), value, BASE)
    tuned = w.start("NT:/Tune/Tuned", "double", "", BASE)
    w.append(tuned, 1.0, BASE)
    if d_tuned_to is not None:  # changed live, five seconds in
        w.append(tuned, d_tuned_to, BASE + 5 * S)
    if mode is not None:
        w.append(w.start("NT:/Tune/Mode", "string", "", BASE), mode, BASE)
    for type_name, text in POSE_SCHEMAS.items():
        w.schema(type_name, text, t_us=BASE)
    w.append(w.start("NT:/Start/Pose", "struct:Pose2d", "", BASE), pose_bytes(*pose, 0.0), BASE)
    w.append(w.start("NT:/FMSInfo/MatchNumber", "int64", "", BASE), match, BASE)
    for name, value in (extra or {}).items():
        w.append(w.start(f"NT:{name}", "double", "", BASE), value, BASE)
    signal = w.start("NT:/Sensor/Raw", "double", "", BASE)
    for k in range(40):
        w.append(signal, float(k) + p, BASE + k * S // 10)
    return LogFile(w.save(path))


@pytest.fixture
def pair(tmp_path):
    first = build(tmp_path / "match7.wpilog", p=0.5, mode="field", d_tuned_to=2.0, pose=(1.0, 2.0), match=7)
    second = build(tmp_path / "match9.wpilog", p=0.8, d=0.2, pose=(1.0, 2.5), match=9, extra={"/Tune/Only9": 3.0})
    return {"l1": first, "l2": second}


def diff(pair, prefix="", name_contains=None, limit=50):
    return queries.diff(pair, prefix, name_contains, limit)


def test_changed_settings_show_both_values_and_unchanged_ones_are_only_counted(pair):
    out = diff(pair)
    assert out["logs"] == {"l1": "match7.wpilog", "l2": "match9.wpilog"}
    assert out["changed"]["/Tune/P"] == {"l1": 0.5, "l2": 0.8}
    assert out["changed"]["/FMSInfo/MatchNumber"] == {"l1": 7, "l2": 9}
    assert "/Tune/I" not in out["changed"] and out["same"] >= 1  # I is 0.1 in both


def test_a_struct_setting_is_compared_by_its_decoded_fields(pair):
    pose = diff(pair)["changed"]["/Start/Pose"]
    assert pose["l1"]["translation.y"] == 2.0 and pose["l2"]["translation.y"] == 2.5


def test_a_setting_only_one_log_has_is_listed_under_only_in(pair):
    out = diff(pair)
    assert out["only_in"] == {"l1": ["/Tune/Mode"], "l2": ["/Tune/Only9"]}


def test_a_setting_that_changed_during_one_log_is_varied_not_changed(pair):
    out = diff(pair)
    assert out["varied_in"] == {"l1": ["/Tune/Tuned"]}  # tuned live in match 7, never touched in match 9
    assert "/Tune/Tuned" not in out["changed"]


def test_a_signal_is_never_compared_between_logs(pair):
    out = diff(pair)  # the sensor reads differently in the two logs, but it is a signal, not a setting
    assert "/Sensor/Raw" not in out["changed"] and "signals_skipped" not in out


def test_prefix_and_name_filters_narrow_the_comparison(pair):
    assert set(diff(pair, prefix="/Tune/")["changed"]) == {"/Tune/P", "/Tune/D"}
    assert set(diff(pair, name_contains="match")["changed"]) == {"/FMSInfo/MatchNumber"}


def test_a_long_section_is_cut_to_the_limit_and_says_how_much_was_left_out(pair):
    out = diff(pair, limit=1)
    assert len(out["changed"]) == 1 and out["omitted"]["changed"] >= 1


def test_identical_logs_have_nothing_changed(tmp_path):
    a = build(tmp_path / "a.wpilog", p=0.5)
    b = build(tmp_path / "b.wpilog", p=0.5)
    out = queries.diff({"l1": a, "l2": b}, "", None, 50)
    assert set(out) == {"logs", "same"} and out["same"] >= 4


def test_busy_topics_are_counted_as_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(diff_queries, "MAX_SETTING_UPDATES", 5)  # the 40-update signal is now "busy"
    a = build(tmp_path / "a.wpilog", p=0.5)
    b = build(tmp_path / "b.wpilog", p=0.9)
    out = queries.diff({"l1": a, "l2": b}, "", None, 50)
    assert out["signals_skipped"] == {"l1": 1, "l2": 1} and "/Sensor/Raw" not in out["changed"]


def text(result):
    return json.loads(result.content[0].text)


async def test_the_tool_compares_two_open_logs_by_id(log_dir):
    build(log_dir / "second.wpilog", p=0.9)
    build(log_dir / "third.wpilog", p=0.1)
    await file_tools.wpilog_open("second.wpilog")
    await file_tools.wpilog_open("third.wpilog")
    out = text(await data_tools.wpilog_diff("l1", log="l2"))
    assert out["logs"] == {"l2": "third.wpilog", "l1": "second.wpilog"}
    assert out["changed"]["/Tune/P"] == {"l2": 0.1, "l1": 0.9}


async def test_the_tool_needs_two_different_open_logs(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    with pytest.raises(ToolError, match="once"):
        await data_tools.wpilog_diff("l1", log="l1")
    with pytest.raises(ToolError, match="No open log 'l9'"):
        await data_tools.wpilog_diff("l9")


def test_a_setting_whose_type_changed_is_reported_even_when_the_number_is_the_same(tmp_path):
    first = build(tmp_path / "a.wpilog", p=0.5, extra={"/Tune/Gain": 3.0})
    w = LogWriter()
    for name, value in (("/Tune/P", 0.5), ("/Tune/I", 0.1), ("/Tune/D", 0.0)):
        w.append(w.start(f"NT:{name}", "double", "", BASE), value, BASE)
    w.append(w.start("NT:/Tune/Gain", "int64", "", BASE), 3, BASE)  # 3.0 became 3, an integer topic
    second = LogFile(w.save(tmp_path / "b.wpilog"))
    out = queries.diff({"l1": first, "l2": second}, "/Tune/", None, 50)
    assert out["changed"] == {"/Tune/Gain": {"l1": 3.0, "l2": 3, "types": {"l1": "double", "l2": "int"}}}


def test_a_setting_the_other_log_declared_but_never_logged_counts_as_only_in(tmp_path):
    first = build(tmp_path / "a.wpilog", p=0.5)
    w = LogWriter()
    for name, value in (("/Tune/P", 0.5), ("/Tune/I", 0.1), ("/Tune/D", 0.0)):
        w.append(w.start(f"NT:{name}", "double", "", BASE), value, BASE)
    w.start("NT:/Tune/Tuned", "double", "", BASE)  # declared, but no value was ever logged
    second = LogFile(w.save(tmp_path / "b.wpilog"))
    out = queries.diff({"l1": first, "l2": second}, "/Tune/", None, 50)
    assert out["only_in"] == {"l1": ["/Tune/Tuned"]} and "changed" not in out


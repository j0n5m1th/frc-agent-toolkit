"""Several logs on one clock: a robot .wpilog, a Driver Station log and a Phoenix-style log synced into one view."""

import json
import time

import pytest
from dslog_builder import START_UNIX_S, build_session
from fastmcp import Client
from fastmcp.exceptions import ToolError

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import store, sync, timeline
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.logfile import LogFile, load
from frc_mcp_wpilog.spec import SPEC
from frc_mcp_wpilog.store import OpenLogs

mcp, _ = SPEC.load()

S = 1_000_000
SHIFT = 3_030_000  # the robot log's clock reads 3.03 s more than the Driver Station log's at the same moment
# the Driver Station session (dslog_builder.build_session): auto 2-4 s, teleop 5-9 s, brownout 7.0-7.2 s


def robot_log(path, *, shift=SHIFT, system_time_error_s=None, enable=True, flag="DS"):
    """A robot log of the same session: the same enables, and a shooter current spike during the brownout."""
    w = LogWriter()
    if enable and flag == "DS":
        en, auto = w.start("DS:enabled", "boolean", "", 0), w.start("DS:autonomous", "boolean", "", 0)
        for t, e, a in ((0, False, False), (2, True, True), (4, False, False), (5, True, False), (9, False, False)):
            w.append(en, e, t * S + shift)
            w.append(auto, a, t * S + shift)
    if enable and flag == "RobotEnable":
        en = w.start("RobotEnable", "boolean", "", 0)
        for t, e in ((0, False), (2, True), (4, False), (5, True), (9, False)):
            w.append(en, e, t * S + shift)
    current = w.start("/Shooter/Current", "double", '{"unit": "A"}', 0)
    for t, amps in ((0, 2.0), (7.0, 60.0), (7.2, 2.0), (10, 2.0)):
        w.append(current, amps, round(t * S) + shift)
    if system_time_error_s is not None:
        clock = w.start("systemTime", "int64", "", 0)
        for t in (1, 5, 9):
            w.append(clock, round((START_UNIX_S + t + system_time_error_s) * S), t * S + shift)
    w.save(path)
    return path


@pytest.fixture


def files(tmp_path, monkeypatch):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    return robot_log(tmp_path / "robot.wpilog"), build_session(tmp_path)


def test_enable_edges_line_up_the_driver_station_log(files):
    robot, ds = map(load, files)
    view, placed = sync.build("s1", {"l1": robot, "l2": ds}, "auto", None)
    assert placed[0].method == "reference"
    assert placed[1].method == "enable" and placed[1].offset_us == SHIFT
    assert placed[1].notes == {"edges_matched": "4/4", "residual_ms": 0.0}
    rows = view.rows(["/DSLog/Status/Brownout", "/Shooter/Current"])
    assert (7 * S + SHIFT, True) in rows["/DSLog/Status/Brownout"]  # the brownout lands on the robot's clock
    assert (7 * S + SHIFT, 60.0) in rows["/Shooter/Current"]


def test_names_both_logs_have_are_prefixed_for_the_later_log(files):
    robot, ds = map(load, files)
    view, _ = sync.build("s1", {"l1": robot, "l2": ds}, "auto", None)
    assert "DS:enabled" in view.topics and "l2:DS:enabled" in view.topics and view.renamed == {"l2": 2}
    assert view.rows(["l2:DS:enabled"])["l2:DS:enabled"][1] == (2 * S + SHIFT, True)


def test_the_wall_clock_syncs_when_the_robot_clock_was_set(files):
    robot = load(robot_log(files[0].with_name("clock.wpilog"), system_time_error_s=0.0))
    _, placed = sync.build("s1", {"l1": robot, "l2": load(files[1])}, "clock", None)
    assert placed[1].method == "clock" and placed[1].offset_us == SHIFT
    _, placed = sync.build("s1", {"l1": robot, "l2": load(files[1])}, "auto", None)
    assert placed[1].method == "enable" and placed[1].notes["clock_differs_ms"] == 0.0  # checked against the clock


def test_a_wrong_wall_clock_loses_to_the_edges_and_says_so(files):
    robot = load(robot_log(files[0].with_name("off.wpilog"), system_time_error_s=3600.0))
    _, placed = sync.build("s1", {"l1": robot, "l2": load(files[1])}, "auto", None)
    assert placed[1].offset_us == SHIFT and "disagree" in placed[1].notes["warning"]


def test_an_invalid_advantagekit_clock_is_not_used(tmp_path):
    w = LogWriter()
    epoch, valid = w.start("/SystemStats/EpochTimeMicros", "int64", "", 0), w.start("/SystemStats/EpochTimeValid", "boolean", "", 0)
    w.append(valid, False, S)
    w.append(epoch, 1_788_630_587_977_743, S)  # a plausible date, but the robot said its clock was not set
    w.append(valid, True, 5 * S)
    w.append(epoch, 1_788_630_600_000_000, 6 * S)
    log = load(w.save(tmp_path / "akit.wpilog"))
    assert sync.wall_offset(log) == 1_788_630_600_000_000 - 6 * S


def test_phoenix_robot_enable_edges_sync_a_converted_hoot(files):
    hoot = load(robot_log(files[0].with_name("hoot.wpilog"), shift=1_000_000, flag="RobotEnable"))
    _, placed = sync.build("s1", {"l1": load(files[0]), "l2": hoot}, "enable", None)
    assert placed[1].offset_us == SHIFT - 1_000_000


def test_logs_with_nothing_to_line_up_need_manual_offsets(files):
    quiet = load(robot_log(files[0].with_name("quiet.wpilog"), enable=False))
    with pytest.raises(WPILogError, match="no enable/disable edges.*offsets_ms"):
        sync.build("s1", {"l1": quiet, "l2": load(files[1])}, "auto", None)
    view, placed = sync.build("s1", {"l1": quiet, "l2": load(files[1])}, "auto", [3030])
    assert placed[1].method == "manual" and view.offsets_us["l2"] == SHIFT
    with pytest.raises(WPILogError, match="one offset per log after the first"):
        sync.build("s1", {"l1": quiet, "l2": load(files[1])}, "auto", [1, 2])


def test_one_edge_that_fits_two_places_is_ambiguous():
    reference = [sync.Edge(1 * S, True), sync.Edge(2 * S, False), sync.Edge(5 * S, True), sync.Edge(6 * S, False)]
    match = sync.match_edges(reference, [sync.Edge(0, True)])
    assert match is not None and match.ambiguous and match.matched == 1


def test_closing_a_log_closes_the_views_made_from_it(files):
    opened = OpenLogs()
    opened.open(files[0])
    opened.open(files[1])
    view_id, _ = opened.add_view(["l1", "l2"], lambda vid, parts: sync.build(vid, parts, "auto", None)[0])
    assert view_id == "s1" and opened.get() is opened.get("s1")  # the view becomes the default log
    with pytest.raises(WPILogError, match="not a synced view"):
        opened.add_view(["s1", "l1"], lambda vid, parts: sync.build(vid, parts, "auto", None)[0])
    opened.close("l2")
    assert opened.ids == ["l1"]


def test_views_do_not_use_up_log_slots_but_are_capped(files, monkeypatch):
    monkeypatch.setattr(store, "MAX_OPEN", 2)
    opened = OpenLogs()
    opened.open(files[0])
    opened.open(files[1])
    make = lambda vid, parts: sync.build(vid, parts, "auto", None)[0]  # noqa: E731
    for _ in range(store.MAX_VIEWS):
        opened.add_view(["l1", "l2"], make)
    with pytest.raises(WPILogError, match="synced views are open already"):
        opened.add_view(["l1", "l2"], make)


async def test_the_sync_tool_lets_one_question_span_files(files):
    async def call(client, name, args):
        return json.loads((await client.call_tool(name, args)).content[0].text)

    async with Client(mcp) as client:
        await call(client, "wpilog_open", {"path": "robot.wpilog"})
        await call(client, "wpilog_open", {"path": files[1].name})
        out = await call(client, "wpilog_sync", {"logs": ["l1", "l2"]})
        assert out["log"] == "s1" and out["synced"][1] == {"log": "l2", "file": files[1].name, "method": "enable",
                                                             "offset_ms": 3030.0, "edges_matched": "4/4", "residual_ms": 0.0}
        assert [p["mode"] for p in out["enabled"]] == ["auto", "teleop"] and "l2" in out["renamed"]
        when = await call(client, "wpilog_when", {"topic": "/DSLog/BatteryVoltage", "op": "<", "value": 7, "align": "teleop",
                                                  "also": [{"topic": "/Shooter/Current", "op": ">", "value": 40}]})
        assert when["intervals_ms"] == [[2000.0, 2200.0]]  # low battery while the shooter drew 60 A, 2 s into teleop
        with pytest.raises(ToolError, match="Give one offset per log"):
            await call(client, "wpilog_sync", {"logs": ["l1", "l2"], "offsets_ms": []})


def test_a_mode_flag_logged_before_enabled_still_marks_a_log_that_started_enabled(tmp_path):
    w = LogWriter()
    auto, enabled = w.start("DS:autonomous", "boolean", "", 0), w.start("DS:enabled", "boolean", "", 0)
    w.append(auto, False, 1_000_000)  # the mode flag comes first...
    w.append(enabled, True, 1_002_000)  # ...then enabled, already true: the robot was enabled before logging began
    w.append(enabled, False, 3 * S)
    w.append(enabled, True, 4 * S)
    w.append(w.start("/x", "double", "", 0), 0.0, 6 * S)
    log = LogFile(w.save(tmp_path / "late.wpilog"))
    first = timeline.phases(log)[0]
    assert first.start_us == 1_002_000 and timeline.already_enabled(log, first)
    assert [(e.t_us, e.rising) for e in sync.enable_edges(log)] == [(3 * S, False), (4 * S, True)]


def test_many_enable_cycles_sync_quickly_and_correctly():
    reference = []
    for i in range(200):  # a long practice session: 200 enables, irregular lengths
        start = i * 7 * S + (i % 5) * 300_000
        reference += [sync.Edge(start, True), sync.Edge(start + 2 * S + (i % 3) * 400_000, False)]
    other = [sync.Edge(e.t_us - 12_345_678 + 20_000, e.rising) for e in reference]
    began = time.perf_counter()
    match = sync.match_edges(reference, other)
    assert time.perf_counter() - began < 5
    assert match is not None and match.offset_us == 12_345_678 - 20_000 and match.matched == 400 and not match.ambiguous

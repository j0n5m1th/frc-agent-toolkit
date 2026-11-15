"""Driver Station logs (.dslog + .dsevents) read through the log server as if they were a .wpilog."""

import json

import pytest
from dslog_builder import build_session, event, header, record
from fastmcp import Client

from frc_mcp_wpilog import config, dslog
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.logfile import load
from frc_mcp_wpilog.overview import overview
from frc_mcp_wpilog.spec import SPEC

mcp, _ = SPEC.load()

NAME = "2024_03_16 14_13_45 Sat"


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    return build_session(tmp_path, NAME)


def test_a_session_converts_to_topics_with_units_and_enabled_periods(session):
    log = load(session)
    info = overview(log, "l1")
    assert info["file"] == f"{NAME}.dslog" and info["duration_s"] == 10.0
    assert info["enabled"] == [{"mode": "auto", "from_s": 2.0, "to_s": 4.0, "for_s": 2.0},
                               {"mode": "teleop", "from_s": 5.0, "to_s": 9.0, "for_s": 4.0}]
    assert "warnings" not in info
    assert log.topics["/DSLog/BatteryVoltage"].unit == "V" and log.topics["/DSLog/PacketLoss"].unit == "%"
    rows = log.rows(["/DSLog/Status/Brownout", "/DSLog/BatteryVoltage", "/DSLog/PacketLoss", "/DSInfo/StartTimeUTC"])
    assert rows["/DSLog/Status/Brownout"][:3] == [(0, False), (7_000_000, True), (7_200_000, False)]
    assert min(v for _, v in rows["/DSLog/BatteryVoltage"]) == 6.5
    assert (6_000_000, 20.0) in rows["/DSLog/PacketLoss"]
    assert rows["/DSInfo/StartTimeUTC"] == [(0, "2024-03-16 19:13:45 UTC")]


def test_rev_pdh_currents_unpack_per_channel(session):
    rows = load(session).rows(["/DSLog/PowerDistributionCurrents"])["/DSLog/PowerDistributionCurrents"]
    at_sag = dict(rows)[7_000_000]
    assert len(at_sag) == 24 and at_sag[3] == 40.0 and sum(at_sag) == 40.0


def test_events_lose_their_markup_and_keep_their_times(session):
    rows = load(session).rows(["/DSEvents"])["/DSEvents"]
    assert rows == [(500_000, "FMS Connected: Qualification - 12:1"), (7_100_000, "ERROR: Brownout detected"),
                    (8_000_000, "Shooter ready")]


@pytest.mark.parametrize(("raw", "clean"), [
    ("<TagVersion>1 <time> 01.000 <message> hello", "hello"),
    ("<details> Loop overrun <location> x <stack> ", "Loop overrun"),
    ("plain text", "plain text"),
    ("<TagVersion>1", ""),
])
def test_clean_event(raw, clean):
    assert dslog.clean_event(raw) == clean


def test_either_file_opens_the_same_session_and_it_is_listed_once(session):
    events = session.with_suffix(".dsevents")
    assert config.find_logs() == [session.resolve()]
    assert config.resolve_log(events.name) == session.resolve()
    assert config.resolve_log(session.name) == session.resolve()
    assert load(events).path == session  # the session is known by its .dslog


def test_a_lone_dsevents_file_is_a_session_of_its_own(tmp_path, monkeypatch):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    lone = tmp_path / "x.dsevents"
    lone.write_bytes(header() + event(1.0, "<message> hi"))
    assert config.find_logs() == [lone.resolve()]
    log = load(lone)
    assert log.rows(["/DSEvents"])["/DSEvents"] == [(1_000_000, "hi")]
    assert "DS:enabled" not in log.topics


def test_a_log_cut_off_mid_record_is_read_up_to_the_cut_and_flagged(tmp_path):
    path = tmp_path / "cut.dslog"
    path.write_bytes(header() + record() * 3 + record()[:7])
    log = load(path)
    assert log.cut_short and log.rows(["/DSLog/BatteryVoltage"])["/DSLog/BatteryVoltage"][0] == (0, 12.5)
    assert list(log.rows(["/DSLog/BatteryVoltage"])["/DSLog/BatteryVoltage"]) == [(0, 12.5), (40_000, 12.5)]  # 3 whole records: first and last kept


def test_battery_spikes_over_20_volts_are_ignored(tmp_path):
    path = tmp_path / "spike.dslog"
    path.write_bytes(header() + record(volts=12.0) + record(volts=200.0))
    rows = load(path).rows(["/DSLog/BatteryVoltage"])["/DSLog/BatteryVoltage"]
    assert {v for _, v in rows} == {12.0}


def test_other_versions_and_short_files_are_refused(tmp_path):
    old = tmp_path / "old.dslog"
    old.write_bytes(header(version=3) + record())
    with pytest.raises(WPILogError, match="version 3; only version 4"):
        load(old)
    tiny = tmp_path / "tiny.dslog"
    tiny.write_bytes(b"\x00\x00")
    with pytest.raises(WPILogError, match="too short"):
        load(tiny)


def test_a_changed_file_is_noticed(session):
    log = load(session)
    assert log.is_current()
    events = session.with_suffix(".dsevents")
    events.write_bytes(events.read_bytes() + event(9.0, "<message> later"))
    assert not log.is_current()


async def test_the_log_tools_work_on_a_driver_station_log(session):
    async def call(client, name, args):
        return json.loads((await client.call_tool(name, args)).content[0].text)

    async with Client(mcp) as client:
        listing = await call(client, "wpilog_list", {})
        assert listing["logs"][0]["file"] == f"{NAME}.dslog"
        await call(client, "wpilog_open", {"path": f"{NAME}.dsevents"})
        when = await call(client, "wpilog_when", {"topic": "/DSLog/BatteryVoltage", "op": "<", "value": 7, "align": "teleop"})
        assert when["intervals_ms"] == [[2000.0, 2200.0]]  # 7.0-7.2 s is 2 s into teleop
        text = await call(client, "wpilog_text", {})  # the default search finds the events, not the start time
        assert text["searched"] == 1 and [i["topic"] for i in text["items"]] == ["/DSEvents"] * 3

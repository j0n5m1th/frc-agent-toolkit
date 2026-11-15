"""Enabled phases, match info and time alignment."""

import struct

import pytest
from log_builder import BASE, POSE_SCHEMAS, S

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import timeline
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.logfile import LogFile


def seconds(log, phase):
    return (phase.mode, (phase.start_us - log.start_us) / S, (phase.end_us - log.start_us) / S)


def test_phases_come_from_the_driver_station_booleans(sample):
    assert [seconds(sample, p) for p in timeline.phases(sample)] == [("auto", 2.0, 5.0), ("teleop", 6.0, 10.0)]


def test_match_info_reads_what_the_driver_station_published(sample):
    assert timeline.match_info(sample) == {"EventName": "Practice Field", "MatchNumber": 12}


def test_origin_aligns_to_the_first_period_of_a_mode(sample):
    assert timeline.origin_us(sample, "start") == (BASE, "log start")
    assert timeline.origin_us(sample, "auto")[0] == BASE + 2 * S
    assert timeline.origin_us(sample, "teleop")[0] == BASE + 6 * S
    assert timeline.origin_us(sample, "enable")[0] == BASE + 2 * S


def test_aligning_to_a_mode_that_never_ran_says_what_did(sample):
    with pytest.raises(WPILogError, match="Enabled modes found: auto, teleop"):
        timeline.origin_us(sample, "test")
    with pytest.raises(WPILogError, match="align must be one of"):
        timeline.origin_us(sample, "sometime")


def test_a_log_without_driver_station_data_has_no_phases_and_only_aligns_to_start(tmp_path):
    w = LogWriter()
    w.append(w.start("NT:/x", "double", "", S), 1.0, S)
    log = LogFile(w.save(tmp_path / "plain.wpilog"))
    assert timeline.phases(log) == [] and timeline.match_info(log) == {}
    with pytest.raises(WPILogError, match="no Driver Station entries"):
        timeline.origin_us(log, "auto")


def test_an_enabled_robot_stays_in_its_phase_to_the_end_of_the_log(tmp_path):
    w = LogWriter()
    enabled, auto = w.start("DS:enabled", "boolean", "", S), w.start("DS:autonomous", "boolean", "", S)
    w.append(auto, False, S)
    w.append(enabled, True, 2 * S)
    w.append(w.start("NT:/x", "double", "", S), 1.0, 9 * S)
    log = LogFile(w.save(tmp_path / "open.wpilog"))
    assert [(p.mode, p.end_us) for p in timeline.phases(log)] == [("teleop", 9 * S)]


def test_phases_can_come_from_a_control_word_struct(tmp_path):
    w = LogWriter()
    for name, text in POSE_SCHEMAS.items():
        w.schema(name, text)
    w.schema("struct:ControlWord", "bool enabled:1;bool autonomous:1;bool test:1;bool eStop:1")
    word = w.start("NT:/FMSInfo/ControlWord", "struct:ControlWord", "", S)
    for t, bits in ((S, 0), (2 * S, 0b011), (4 * S, 0), (6 * S, 0b001)):  # enabled+auto, off, enabled (teleop)
        w.append(word, struct.pack("<B", bits), t)
    w.append(w.start("NT:/x", "double", "", S), 1.0, 8 * S)
    log = LogFile(w.save(tmp_path / "word.wpilog"))
    assert [(p.mode, p.start_us, p.end_us) for p in timeline.phases(log)] == [("auto", 2 * S, 4 * S), ("teleop", 6 * S, 8 * S)]


def test_phases_are_worked_out_once_per_log(sample, monkeypatch):
    from frc_mcp_wpilog import timeline

    first = timeline.phases(sample)
    monkeypatch.setattr(timeline, "_find_phases", lambda log: pytest.fail("recomputed"))
    assert timeline.phases(sample) is first


def test_a_control_word_logged_as_a_double_still_gives_the_periods(tmp_path):
    w = LogWriter()
    word = w.start("NT:/FMSInfo/FMSControlData", "double", "", 0)
    for t, value in ((1, 0x30), (2, 0x33), (4, 0x30), (5, 0x31), (7, 0x30)):
        w.append(word, float(value), t * S)
    log = LogFile(w.save(tmp_path / "double_word.wpilog"))
    assert [(p.mode, p.start_us, p.end_us) for p in timeline.phases(log)] == [("auto", 2 * S, 4 * S), ("teleop", 5 * S, 7 * S)]


def test_first_driver_station_mode_zero_is_teleop_as_advantagescope_reads_it():
    assert timeline._mode(timeline._dscomm_control_word(0 << 1 | 1)) == "teleop"
    assert timeline._mode(timeline._dscomm_control_word(1 << 1 | 1)) == "auto"
    assert timeline._mode(timeline._dscomm_control_word(3 << 1 | 1)) == "utility"

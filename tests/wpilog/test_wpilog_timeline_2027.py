"""Phases from the WPILib 2027 ControlWord (robotMode enum, Utility mode)."""

import struct

from log_builder import POSE_SCHEMAS, S

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import timeline
from frc_mcp_wpilog.logfile import LogFile

# Copied from a real alpha-7 log written by DataLogManager.
CONTROL_WORD_2027 = ("uint64 opModeHash:56;enum{unknown=0,autonomous=1,teleoperated=2,utility=3} uint64 robotMode:2;"
                     "bool enabled:1;bool eStop:1;bool fmsAttached:1;bool dsAttached:1;")


def word(mode: int, enabled: bool) -> bytes:
    return struct.pack("<Q", (mode << 56) | (int(enabled) << 58) | (1 << 61))


def test_a_2027_utility_opmode_is_its_own_phase_not_teleop(tmp_path):
    w = LogWriter()
    for name, text in POSE_SCHEMAS.items():
        w.schema(name, text)
    w.schema("struct:ControlWord", CONTROL_WORD_2027)
    cw = w.start("NT:/DriverStation/ControlWord", "struct:ControlWord", "", S)
    for t, mode, on in ((S, 1, False), (2 * S, 1, True), (4 * S, 2, True), (6 * S, 3, True)):
        w.append(cw, word(mode, on), t)
    w.append(w.start("NT:/x", "double", "", S), 1.0, 8 * S)
    log = LogFile(w.save(tmp_path / "cw2027.wpilog"))
    assert [p.mode for p in timeline.phases(log)] == ["auto", "teleop", "utility"]


def test_each_period_names_the_opmode_that_ran(tmp_path):
    w = LogWriter()
    for name, text in POSE_SCHEMAS.items():
        w.schema(name, text)
    w.schema("struct:ControlWord", CONTROL_WORD_2027)
    cw = w.start("NT:/DriverStation/ControlWord", "struct:ControlWord", "", S)
    opmode = w.start("NT:/DriverStation/OpMode", "string", "", S)
    w.append(opmode, "Leave Start", S)
    w.append(cw, word(1, True), 2 * S)
    w.append(cw, word(1, False), 4 * S)
    w.append(opmode, "Teleop", 5 * S)
    w.append(cw, word(2, True), 6 * S)
    w.append(w.start("NT:/x", "double", "", S), 1.0, 8 * S)
    log = LogFile(w.save(tmp_path / "opmode.wpilog"))
    assert [(p.mode, p.opmode) for p in timeline.phases(log)] == [("auto", "Leave Start"), ("teleop", "Teleop")]


def test_phases_come_from_advantagekit_2027_entries(tmp_path):
    # What an AdvantageKit 2027 robot logs: Enabled plus a RobotMode string, and the OpMode name.
    w = LogWriter()
    enabled = w.start("/DriverStation/Enabled", "boolean", "", S)
    mode = w.start("/DriverStation/RobotMode", "string", "", S)
    opmode = w.start("/DriverStation/OpMode", "string", "", S)
    w.append(enabled, False, S)
    w.append(mode, "AUTONOMOUS", S)
    w.append(opmode, "2 - Drive To Pose", S)
    w.append(enabled, True, 2 * S)
    w.append(enabled, False, 4 * S)
    w.append(mode, "UTILITY", 5 * S)
    w.append(enabled, True, 6 * S)
    w.append(w.start("/x", "double", "", S), 1.0, 8 * S)
    log = LogFile(w.save(tmp_path / "akit2027.wpilog"))
    assert [(p.mode, p.start_us, p.end_us) for p in timeline.phases(log)] == [("auto", 2 * S, 4 * S), ("utility", 6 * S, 8 * S)]
    assert timeline.origin_us(log, "utility")[0] == 6 * S


def test_phases_come_from_advantagekit_booleans_before_2027(tmp_path):
    w = LogWriter()
    enabled, auto = w.start("/DriverStation/Enabled", "boolean", "", S), w.start("/DriverStation/Autonomous", "boolean", "", S)
    w.append(auto, True, S)
    w.append(enabled, True, 2 * S)
    w.append(auto, False, 4 * S)
    w.append(w.start("/x", "double", "", S), 1.0, 8 * S)
    log = LogFile(w.save(tmp_path / "akit.wpilog"))
    assert [p.mode for p in timeline.phases(log)] == ["auto", "teleop"]


def test_a_cleared_opmode_does_not_carry_its_old_name_into_the_next_period(tmp_path):
    # The Driver Station clears the OpMode (empty name) between runs, and the next name can land just after enable.
    w = LogWriter()
    for name, text in POSE_SCHEMAS.items():
        w.schema(name, text)
    w.schema("struct:ControlWord", CONTROL_WORD_2027)
    cw = w.start("NT:/DriverStation/ControlWord", "struct:ControlWord", "", S)
    opmode = w.start("NT:/DriverStation/OpMode", "string", "", S)
    w.append(opmode, "Auto A", S)
    w.append(cw, word(1, True), 2 * S)
    w.append(cw, word(1, False), 4 * S)
    w.append(opmode, "", 4 * S)
    w.append(cw, word(2, True), 6 * S)
    w.append(opmode, "Teleop", 6 * S + S // 10)
    w.append(w.start("NT:/x", "double", "", S), 1.0, 8 * S)
    log = LogFile(w.save(tmp_path / "cleared.wpilog"))
    assert [(p.mode, p.opmode) for p in timeline.phases(log)] == [("auto", "Auto A"), ("teleop", "Teleop")]

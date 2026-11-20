"""wpilog_swerve: how closely each swerve module followed its command, found by a logging convention and never by a suggestive name."""

import json
import math
import struct

import pytest
from log_builder import BASE, POSE_SCHEMAS, S

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog.tools import data_tools, file_tools

MEASURED = "/RealOutputs/SwerveStates/Measured"
SETPOINT = "/RealOutputs/SwerveStates/SetpointsOptimized"


def text(result):
    return json.loads(result.content[0].text)


def at(seconds):
    return BASE + int(seconds * S)


def swerve_writer():
    """A log writer that already knows the SwerveModuleState struct."""
    w = LogWriter()
    for name, schema in POSE_SCHEMAS.items():
        w.schema(name, schema, t_us=BASE)
    w.schema("struct:SwerveModuleState", "double speed;Rotation2d angle", t_us=BASE)
    return w


def states(*modules):
    """The bytes of one SwerveModuleState[] update: `(speed m/s, angle rad)` per module."""
    return b"".join(struct.pack("<dd", speed, angle) for speed, angle in modules)


def build(path, *, measured=MEASURED, setpoint=SETPOINT, lag=2, stopped=False, disabled_noise=True):
    """Four modules for 10 s, enabled 1-9 s. Commanded 2 m/s at 0.5 rad. Module `lag` runs 1 m/s slow and 30 degrees off; the others are off by 0.05 m/s and 2 degrees.
    Before 1 s and after 9 s (disabled) every module is far off, which must not count."""
    w = swerve_writer()
    enabled = w.start("DS:enabled", "boolean", "", BASE)
    for t, on in ((0, False), (1, True), (9, False)):
        w.append(enabled, on, at(t))
    m = w.start(measured, "struct:SwerveModuleState[]", "", BASE)
    s = w.start(setpoint, "struct:SwerveModuleState[]", "", BASE)
    for n in range(500):
        t = n * 0.02
        on = 1 <= t <= 9
        speed = 0.0 if stopped else 2.0
        w.append(s, states(*[(speed, 0.5)] * 4), at(t))
        got = []
        for k in range(4):
            if not on and disabled_noise:
                got.append((9.0, 3.0))
            elif k == lag:
                got.append((speed - 1.0, 0.5 + math.radians(30)))
            else:
                got.append((speed - 0.05, 0.5 + math.radians(2)))
        w.append(m, states(*got), at(t) + 1000)
    w.save(path)
    return path.name


@pytest.fixture
def swerve_log(log_dir):
    return build(log_dir / "swerve.wpilog")


async def test_the_lagging_module_is_named_with_its_errors_and_the_disabled_time_is_left_out(swerve_log):
    await file_tools.wpilog_open(swerve_log)
    out = text(await data_tools.wpilog_swerve())
    assert out["measured"] == MEASURED and out["setpoint"] == SETPOINT and out["chosen_by"] == "convention" and out["scope"] == "enabled"
    by_module = {m["module"]: m for m in out["modules"]}
    assert set(by_module) == {0, 1, 2, 3}
    assert by_module[2]["speed_err_p95_mps"] == pytest.approx(1.0, abs=0.01) and by_module[2]["angle_err_p95_deg"] == pytest.approx(30.0, abs=0.2)
    assert by_module[0]["speed_err_p95_mps"] == pytest.approx(0.05, abs=0.01) and by_module[0]["angle_err_p95_deg"] == pytest.approx(2.0, abs=0.2)
    assert by_module[0]["top_speed_mps"] == pytest.approx(1.95, abs=0.01)  # the 9 m/s of the disabled time is not counted
    assert any("module 2 lags the others in speed" in f for f in out["flags"]) and any("module 2 lags the others in angle" in f for f in out["flags"])
    assert not any("module 0" in f for f in out["flags"])


async def test_a_window_replaces_the_enabled_default(swerve_log):
    await file_tools.wpilog_open(swerve_log)
    out = text(await data_tools.wpilog_swerve(since_ms=0, until_ms=500))  # before enabling: every module is far off
    assert out["scope"] == "window" and out["modules"][0]["speed_err_p95_mps"] == pytest.approx(7.0, abs=0.1)


async def test_ctre_names_are_a_convention_too(log_dir):
    name = build(log_dir / "ctre.wpilog", measured="/DriveState/ModuleStates", setpoint="/DriveState/ModuleTargets")
    await file_tools.wpilog_open(name)
    out = text(await data_tools.wpilog_swerve())
    assert out["measured"] == "/DriveState/ModuleStates" and out["setpoint"] == "/DriveState/ModuleTargets"


async def test_other_names_are_listed_as_candidates_and_not_used_until_named(log_dir):
    name = build(log_dir / "odd.wpilog", measured="/Drive/Actual", setpoint="/Drive/Wanted")
    await file_tools.wpilog_open(name)
    out = text(await data_tools.wpilog_swerve())
    assert out["status"] == "no_match" and out["candidates"] == ["/Drive/Actual", "/Drive/Wanted"] and "pass measured= and setpoint=" in out["reason"]
    named = text(await data_tools.wpilog_swerve(measured="/Drive/Actual", setpoint="/Drive/Wanted"))
    assert named["chosen_by"] == "explicit" and len(named["modules"]) == 4


async def test_a_named_topic_must_exist_and_be_a_swerve_array(swerve_log):
    await file_tools.wpilog_open(swerve_log)
    with pytest.raises(Exception, match="not in this log"):
        await data_tools.wpilog_swerve(measured="/No/Such", setpoint=SETPOINT)
    with pytest.raises(Exception, match="a swerve array is struct:SwerveModuleState"):
        await data_tools.wpilog_swerve(measured="DS:enabled", setpoint=SETPOINT)


async def test_the_angle_is_judged_only_while_a_module_is_told_to_move(log_dir):
    await file_tools.wpilog_open(build(log_dir / "stopped.wpilog", stopped=True))
    out = text(await data_tools.wpilog_swerve())
    assert all("angle_err_p95_deg" not in m for m in out["modules"])


async def test_an_angle_across_the_wrap_is_a_small_error(log_dir):
    w = swerve_writer()
    m = w.start(MEASURED, "struct:SwerveModuleState[]", "", BASE)
    s = w.start(SETPOINT, "struct:SwerveModuleState[]", "", BASE)
    for n in range(30):
        w.append(s, states(*[(1.0, 3.10)] * 3), at(n * 0.1))
        w.append(m, states(*[(1.0, -3.10)] * 3), at(n * 0.1) + 1000)  # 0.083 rad apart across +-pi, not 6.2 rad
    w.save(log_dir / "wrap.wpilog")
    await file_tools.wpilog_open("wrap.wpilog")
    angle = text(await data_tools.wpilog_swerve())["modules"][0]["angle_err_p95_deg"]
    assert angle == pytest.approx(math.degrees(2 * math.pi - 6.2), abs=0.1)


async def test_scan_leaves_the_drivetrain_to_the_swerve_tool(swerve_log):
    await file_tools.wpilog_open(swerve_log)
    out = text(await data_tools.wpilog_scan())
    assert "swerve" not in out and not any("swerve" in f for f in out["flags"])  # one tool, one job: wpilog_swerve checks the modules


async def test_align_alone_does_not_widen_the_enabled_default(swerve_log):
    await file_tools.wpilog_open(swerve_log)
    out = text(await data_tools.wpilog_swerve(align="enable"))
    assert out["scope"] == "enabled" and out["modules"][0]["top_speed_mps"] == pytest.approx(1.95, abs=0.01)


async def test_other_swerve_arrays_in_the_log_are_named_not_silently_ignored(log_dir):
    w = swerve_writer()
    topics = [w.start(n, "struct:SwerveModuleState[]", "", BASE) for n in (MEASURED, SETPOINT, "/DriveState/ModuleStates", "/DriveState/ModuleTargets")]
    for n in range(30):
        for entry in topics:
            w.append(entry, states(*[(1.0, 0.2)] * 4), at(n * 0.1))
    w.save(log_dir / "both.wpilog")
    await file_tools.wpilog_open("both.wpilog")
    out = text(await data_tools.wpilog_swerve())
    assert out["measured"] == MEASURED and out["others"] == ["/DriveState/ModuleStates", "/DriveState/ModuleTargets"]


async def test_arrays_with_no_commanded_update_before_the_measured_ones_say_no_match(log_dir):
    w = swerve_writer()
    m = w.start(MEASURED, "struct:SwerveModuleState[]", "", BASE)
    s = w.start(SETPOINT, "struct:SwerveModuleState[]", "", BASE)
    for n in range(20):
        w.append(m, states(*[(1.0, 0.2)] * 4), at(n * 0.1))
    w.append(s, states(*[(1.0, 0.2)] * 4), at(50))  # commanded long after every measured update
    w.save(log_dir / "late.wpilog")
    await file_tools.wpilog_open("late.wpilog")
    out = text(await data_tools.wpilog_swerve())
    assert out["status"] == "no_match" and "commanded" in out["reason"] and "flags" not in out


async def test_a_handful_of_updates_is_not_enough_to_call_a_drivetrain_clean(log_dir):
    w = LogWriter()
    w.schema("struct:SwerveModuleState", "double speed;Rotation2d angle", t_us=BASE)
    m = w.start(MEASURED, "struct:SwerveModuleState[]", "", BASE)
    s = w.start(SETPOINT, "struct:SwerveModuleState[]", "", BASE)
    for n in range(4):
        w.append(s, states(*[(1.0, 0.2)] * 4), at(n))
        w.append(m, states(*[(1.0, 0.2)] * 4), at(n) + 1000)
    w.save(log_dir / "short.wpilog")
    await file_tools.wpilog_open("short.wpilog")
    out = text(await data_tools.wpilog_swerve())
    assert out["status"] == "no_match" and "at least 10" in out["reason"]

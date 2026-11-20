"""wpilog_correlate and wpilog_pose: the signals are named by the caller (or found by a pose convention) and the shared checks run on the log."""

import json
import math
import struct

import pytest
from log_builder import BASE, POSE_SCHEMAS, S

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog.tools import data_tools, file_tools

POSE = "/DriveState/Pose"
VISION = "/Vision/Pose"


def text(result):
    return json.loads(result.content[0].text)


def at(seconds):
    return BASE + int(seconds * S)


def pose_writer():
    w = LogWriter()
    for name, schema in POSE_SCHEMAS.items():
        w.schema(name, schema, t_us=BASE)
    return w


def build(path):
    """Enabled 1-9 s. Signal b follows signal a by 0.5 s. The robot pose drives straight and leaps 1 m at 5 s; the vision pose is 0.3 m off."""
    w = pose_writer()
    enabled = w.start("DS:enabled", "boolean", "", BASE)
    for t, on in ((0, False), (1, True), (9, False)):
        w.append(enabled, on, at(t))
    a, b = w.start("/Motor/Current", "double", "", BASE), w.start("/Battery/Sag", "double", "", BASE)
    pose, vision = w.start(POSE, "struct:Pose2d", "", BASE), w.start(VISION, "struct:Pose2d", "", BASE)
    for n in range(500):
        t = n * 0.02
        w.append(a, math.sin(t * 2.0), at(t))
        w.append(b, math.sin((t - 0.5) * 2.0), at(t))
        x = t * 0.8 + (1.0 if t >= 5.0 else 0.0)
        w.append(pose, struct.pack("<ddd", x, 1.0, 0.0), at(t))
        w.append(vision, struct.pack("<ddd", x + 0.3, 1.0, 0.0), at(t))
    w.save(path)
    return path.name


@pytest.fixture
def drive_log(log_dir):
    return build(log_dir / "drive.wpilog")


async def test_a_lagging_signal_is_found_and_the_window_is_the_enabled_time(drive_log):
    await file_tools.wpilog_open(drive_log)
    out = text(await data_tools.wpilog_correlate(a="/Motor/Current", b="/Battery/Sag"))
    assert out["scope"] == "enabled" and out["best_lag"]["lag_s"] == pytest.approx(0.5, abs=0.06) and out["best_lag"]["meaning"] == "b follows a"


async def test_correlation_needs_signals_that_exist_and_are_numbers(drive_log):
    await file_tools.wpilog_open(drive_log)
    with pytest.raises(Exception, match="not in this log"):
        await data_tools.wpilog_correlate(a="/No/Such", b="/Battery/Sag")
    with pytest.raises(Exception, match="must be a number"):
        await data_tools.wpilog_correlate(a=POSE, b="/Battery/Sag")


async def test_the_robot_pose_is_found_by_convention_and_its_jump_and_the_vision_offset_are_reported(drive_log):
    await file_tools.wpilog_open(drive_log)
    out = text(await data_tools.wpilog_pose(reference=VISION))
    assert out["pose"] == POSE and out["chosen_by"] == "convention" and out["others"] == [VISION]
    assert out["jumps"]["count"] == 1 and out["jumps"]["largest"][0]["meters"] == pytest.approx(1.0, abs=0.05)
    assert out["vs_reference"]["meters"]["mean"] == pytest.approx(0.3, abs=0.01)


async def test_a_pose_that_is_not_by_convention_is_listed_until_named(log_dir):
    w = pose_writer()
    entry = w.start("/Weird/Place", "struct:Pose2d", "", BASE)
    for n in range(40):
        w.append(entry, struct.pack("<ddd", n * 0.01, 0.0, 0.0), at(n * 0.02))
    w.save(log_dir / "weird.wpilog")
    await file_tools.wpilog_open("weird.wpilog")
    out = text(await data_tools.wpilog_pose())
    assert out["status"] == "no_match" and out["candidates"] == ["/Weird/Place"] and "pass pose=" in out["reason"]
    named = text(await data_tools.wpilog_pose(pose="/Weird/Place"))
    assert named["chosen_by"] == "explicit" and named["jumps"] == {"count": 0}


async def test_a_pose_check_refuses_a_topic_that_is_not_a_pose(drive_log):
    await file_tools.wpilog_open(drive_log)
    with pytest.raises(Exception, match="a pose is struct:Pose2d"):
        await data_tools.wpilog_pose(pose="/Battery/Sag")

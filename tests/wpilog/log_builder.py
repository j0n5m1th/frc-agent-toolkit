"""Builds small deterministic WPILOG files for the log server's tests (no binary fixtures in the repo)."""

import struct
from pathlib import Path

from frc_mcp_wpilib.wpilog_writer import LogWriter

S = 1_000_000  # one second in microseconds
BASE = 5 * S  # the robot clock does not start at zero: the log's first record is at 5 s

POSE_SCHEMAS = {
    "struct:Translation2d": "double x;double y",
    "struct:Rotation2d": "double value",
    "struct:Pose2d": "Translation2d translation;Rotation2d rotation",
}


def pose_bytes(x: float, y: float, theta: float) -> bytes:
    return struct.pack("<ddd", x, y, theta)


def build_sample_log(path: Path) -> Path:
    """A 12 second "match": disabled 0-2 s, auto 2-5 s, disabled 5-6 s, teleop 6-10 s (seconds after BASE).

    Signals (all under NT:/Fake/ unless noted):
      Elevator/Current  double [A]  5.0 with a 42.5 spike at 3.0-3.5 s and 41.0 at 7.0-7.2 s
      Arm/Angle         double      a ramp, 10 degrees per second (every 0.5 s)
      Match/Mode        string      "disabled" -> "auto" -> "teleop"
      Debug/Raw         int64       a counter
      Flags             boolean[]   one update
      Drive/Pose        struct:Pose2d  x grows 1 m/s during auto
      Drive/Speeds      struct:ChassisSpeeds (vx only) is not provided; Pose is the struct under test
      DS:enabled / DS:autonomous / DS:test   the Driver Station booleans
      messages          string      console lines, including a warning and an error
      /FMSInfo/EventName, /FMSInfo/MatchNumber   match info
    """
    w = LogWriter()
    for name, text in POSE_SCHEMAS.items():
        w.schema(name, text, t_us=BASE)

    def at(seconds: float) -> int:
        return BASE + int(seconds * S)

    # --- Driver Station state
    enabled = w.start("DS:enabled", "boolean", "", BASE)
    auto = w.start("DS:autonomous", "boolean", "", BASE)
    test = w.start("DS:test", "boolean", "", BASE)
    w.append(auto, False, BASE)
    w.append(test, False, BASE)
    w.append(enabled, False, BASE)
    w.append(auto, True, at(2))
    w.append(enabled, True, at(2))
    w.append(enabled, False, at(5))
    w.append(auto, False, at(5))
    w.append(enabled, True, at(6))
    w.append(enabled, False, at(10))

    # --- match info
    event = w.start("NT:/FMSInfo/EventName", "string", "", BASE)
    number = w.start("NT:/FMSInfo/MatchNumber", "int64", "", BASE)
    w.append(event, "Practice Field", BASE)
    w.append(number, 12, BASE)

    # --- signals
    current = w.start("NT:/Fake/Elevator/Current", "double", '{"unit":"A"}', BASE)
    for t10 in range(0, 121):  # every 0.1 s for 12 s
        seconds = t10 / 10
        spike = 42.5 if 3.0 <= seconds < 3.5 else 41.0 if 7.0 <= seconds < 7.2 else 5.0
        w.append(current, spike, at(seconds))
    angle = w.start("NT:/Fake/Arm/Angle", "double", "", BASE)
    for step in range(25):
        w.append(angle, step * 5.0, at(step * 0.5))
    mode = w.start("NT:/Fake/Match/Mode", "string", "", BASE)
    for seconds, text in ((0, "disabled"), (2, "auto"), (5, "disabled"), (6, "teleop")):
        w.append(mode, text, at(seconds))
    raw = w.start("NT:/Fake/Debug/Raw", "int64", "", BASE)
    for n in range(10):
        w.append(raw, n, at(n))
    flags = w.start("NT:/Fake/Flags", "boolean[]", "", BASE)
    w.append(flags, [True, False], at(1))
    pose = w.start("NT:/Fake/Drive/Pose", "struct:Pose2d", "", BASE)
    for step in range(0, 31):  # every 0.1 s from 2 s to 5 s: x = 0..3 m
        w.append(pose, pose_bytes(step / 10, 1.0, 0.0), at(2 + step / 10))
    messages = w.start("messages", "string", "", BASE)
    w.append(messages, "robot program starting", BASE)
    w.append(messages, "WARNING: battery low", at(4))
    w.append(messages, "ERROR: arm limit switch not found", at(8))
    return w.save(path)

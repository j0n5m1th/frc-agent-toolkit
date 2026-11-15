"""The practice log: `frc-mcp demo log [--out practice.wpilog] [--seconds 30]`.

Writes a made-up 30 second match to a `.wpilog` (default `practice.wpilog`, in the current folder), so you can try the log
server with no robot. Timeline: 0-2 s disabled, 2-17 s autonomous, 17-19 s disabled, 19-29 s teleop. Signals are under /Fake/
(elevator current, battery voltage, arm angle, drive pose and speeds, mode, a tunable setpoint, a counter) and match the
practice robot (`frc_mcp_nt4.demo`). Also DS:enabled / DS:autonomous / DS:test, /FMSInfo/*, and a `messages` text log with
one warning (12 s) and one error (22 s).
"""

import argparse
import struct
from collections.abc import Sequence
from pathlib import Path

from frc_mcp_wpilib.practice_signals import BASE_A, arm, elevator
from frc_mcp_wpilib.wpilog_writer import LogWriter

S = 1_000_000
BASE_US = 10 * S  # the robot's clock does not start at zero
AUTO_START, AUTO_END, TELEOP_START, TELEOP_END = 2.0, 17.0, 19.0, 29.0
DEFAULT_SECONDS = 30.0
STEP_S = 0.02  # the fast signals are logged at 50 Hz
DEFAULT_OUT = Path("practice.wpilog")
SCHEMAS = {
    "struct:Translation2d": "double x;double y",
    "struct:Rotation2d": "double value",
    "struct:Pose2d": "Translation2d translation;Rotation2d rotation",
    "struct:ChassisSpeeds": "double vx;double vy;double omega",
}


elevator_current = elevator  # the practice robot's signals, shared through `frc_mcp_wpilib.practice_signals`
arm_angle = arm


def mode_at(t: float) -> str:
    if AUTO_START <= t < AUTO_END:
        return "auto"
    return "teleop" if TELEOP_START <= t < TELEOP_END else "disabled"


def speed_vx(t: float) -> float:
    if AUTO_START <= t < AUTO_END:
        return min(2.0, (t - AUTO_START) * 2.0)  # ramps to 2 m/s in a second, then holds
    return 1.0 if TELEOP_START <= t < TELEOP_END else 0.0


def pose_x(t: float) -> float:
    """Distance driven: the integral of the autonomous speed (1.5 m/s average once up to speed), then it holds."""
    driven = max(0.0, min(t, AUTO_END) - AUTO_START)
    return round(1.5 * driven, 4)


def build(seconds: float = DEFAULT_SECONDS) -> LogWriter:
    w = LogWriter()
    for name, text in SCHEMAS.items():
        w.schema(name, text, t_us=BASE_US)

    def at(t: float) -> int:
        return BASE_US + round(t * S)

    # Driver Station state and match facts
    enabled, auto, test = (w.start(f"DS:{n}", "boolean", "", BASE_US) for n in ("enabled", "autonomous", "test"))
    for entry, value, t in ((test, False, 0), (auto, False, 0), (enabled, False, 0), (auto, True, AUTO_START),
                            (enabled, True, AUTO_START), (enabled, False, AUTO_END), (auto, False, AUTO_END),
                            (enabled, True, TELEOP_START), (enabled, False, TELEOP_END)):
        w.append(entry, value, at(t))
    w.append(w.start("NT:/FMSInfo/EventName", "string", "", BASE_US), "Practice Field", BASE_US)
    w.append(w.start("NT:/FMSInfo/MatchNumber", "int64", "", BASE_US), 7, BASE_US)
    w.append(w.start("NT:/FMSInfo/MatchType", "int64", "", BASE_US), 1, BASE_US)

    # fast signals
    current = w.start("NT:/Fake/Elevator/Current", "double", '{"unit":"A"}', BASE_US)
    voltage = w.start("NT:/Fake/Battery/Voltage", "double", '{"unit":"V"}', BASE_US)
    angle = w.start("NT:/Fake/Arm/Angle", "double", '{"unit":"deg"}', BASE_US)
    pose = w.start("NT:/Fake/Drive/Pose", "struct:Pose2d", "", BASE_US)
    speeds = w.start("NT:/Fake/Drive/Speeds", "struct:ChassisSpeeds", "", BASE_US)
    raw = w.start("NT:/Fake/Debug/Raw", "int64", "", BASE_US)
    steps = round(seconds / STEP_S)
    for n in range(steps + 1):
        t = n * STEP_S
        amps = elevator_current(t)
        w.append(current, amps, at(t))
        w.append(voltage, round(12.6 - 0.03 * (amps - BASE_A), 3), at(t))
        w.append(angle, round(arm_angle(t), 3), at(t))
        w.append(pose, struct.pack("<ddd", pose_x(t), 2.0, 0.0), at(t))
        w.append(speeds, struct.pack("<ddd", speed_vx(t), 0.0, 0.0), at(t))
        w.append(raw, n, at(t))

    # slow signals and text
    mode = w.start("NT:/Fake/Match/Mode", "string", "", BASE_US)
    previous = None
    for n in range(steps + 1):
        now = mode_at(n * STEP_S)
        if now != previous:
            w.append(mode, now, at(n * STEP_S))
            previous = now
    setpoint = w.start("NT:/Fake/Tunable/Setpoint", "double", "", BASE_US)
    for level, t in ((1.0, 0.0), (2.5, 10.0), (1.0, 12.0)):
        w.append(setpoint, level, at(t))
    messages = w.start("messages", "string", "", BASE_US)
    for text, t in (("robot program starting", 0.0), ("autonomous started", AUTO_START),
                    ("WARNING: battery voltage below 12 V", 12.0), ("teleop started", TELEOP_START),
                    ("ERROR: arm limit switch not found", 22.0)):
        w.append(messages, text, at(t))
    return w


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="frc-mcp demo log", description="Write a deterministic practice .wpilog (no robot needed).")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"where to write it (default: {DEFAULT_OUT} in the current folder)")
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="length of the log (default: 30; at least 30)")
    args = ap.parse_args(argv)
    if not 30 <= args.seconds <= 600:
        ap.error("--seconds must be between 30 and 600 so the match phases fit")
    path = build(args.seconds).save(args.out)
    print(f"Wrote the practice log to {path.resolve()} ({path.stat().st_size / 1024:.0f} KB).")
    print(f"Check it: frc-mcp wpilog check {path.name}   (in the folder that holds it, or set WPILOG_DIR to that folder)")
    return 0


__all__ = ["DEFAULT_OUT", "DEFAULT_SECONDS", "SCHEMAS", "build", "main"]

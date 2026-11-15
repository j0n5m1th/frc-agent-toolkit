"""The practice robot: `frc-mcp demo robot [--port 5810]`.

A made-up NT4 server whose signals loop forever, so you can try the NT4 server with no robot. It listens only on 127.0.0.1;
use host `localhost` and the port it prints. The evaluations use it too (`evaluations/fake_robot.py`).

Signals (all periodic): /Fake/Elevator/Current (5.0 A, 42.5 A for the first 0.4 s of every 5 s), /Fake/Arm/Angle (triangle
wave -30..90 deg, period 10 s), /Fake/Match/Mode (9 s cycle: disabled 0-3, auto 3-5, teleop 5-9), /Fake/Debug/Raw (counter,
cached=false), /Fake/Auto Chooser (Left/Center/Right). Tunables: /Fake/Tunable/Setpoint 1.0, Gain 2.0, Limit 10.0 (retained,
not persistent); /Fake/Output/Command = Setpoint * Gain at 50 Hz. The robot never subscribes to Limit (see `nt4_inspect`).
"""

import argparse
import math
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

import ntcore

from frc_mcp_wpilib.practice_signals import ARM_LO, BASE_A, arm, elevator, mode

DEFAULT_PORT = 5810  # the NT4 server's own default, so no setting needs to change
LOOP_PERIOD_S = 0.02  # the "robot" loop runs at 50 Hz


def build_parser(default_port: int = DEFAULT_PORT) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="frc-mcp demo robot", description="A practice NT4 robot on your own computer. Press Ctrl+C to stop it.")
    ap.add_argument("--port", type=int, default=default_port, help=f"the NT4 port to listen on (default: {default_port})")
    ap.add_argument("--phase-seconds", type=float, default=0.0, help="offset all signal phases by this many seconds")
    ap.add_argument("--run-id", default=None, help="publish a unique evaluation readiness marker")
    ap.add_argument("--persistence-file", type=Path, default=None)
    return ap


def parse_args(argv: Sequence[str] | None = None, default_port: int = DEFAULT_PORT) -> argparse.Namespace:
    ap = build_parser(default_port)
    args = ap.parse_args(argv)
    if not math.isfinite(args.phase_seconds):
        ap.error("--phase-seconds must be finite")
    if not 1 <= args.port <= 65535:
        ap.error("--port must be between 1 and 65535")
    return args


def main(argv: Sequence[str] | None = None, default_port: int = DEFAULT_PORT) -> int:
    args = parse_args(argv, default_port)
    # persistent topics make ntcore write a save file; keep it out of the working directory (an empty name wrote ./.tmp)
    # and remove it again when the robot stops (Ctrl+C)
    try:
        if args.persistence_file:
            return run(args, args.persistence_file)
        with tempfile.TemporaryDirectory(prefix="fake-robot-") as tmp:
            return run(args, Path(tmp) / "networktables.json")
    except KeyboardInterrupt:  # Ctrl+C is how the harness stops the server: the normal end, not a failure
        print("fake robot stopped", flush=True)
        return 130  # the usual code for a stopped-by-signal run


def run(args: argparse.Namespace, persist: Path) -> int:
    inst = ntcore.NetworkTableInstance.create()
    inst.startServer(persist_filename=str(persist), listen_address="127.0.0.1", port3=0, port4=args.port)

    def pub(name, type_str, value, props=None):
        p = inst.getTopic(name).genericPublishEx(type_str, props or {})
        p.set(value)
        return p

    D, S, N, B = ntcore.Value.makeDouble, ntcore.Value.makeString, ntcore.Value.makeInteger, ntcore.Value.makeStringArray
    p_elev = pub("/Fake/Elevator/Current", "double", D(BASE_A))
    p_arm = pub("/Fake/Arm/Angle", "double", D(ARM_LO))
    p_mode = pub("/Fake/Match/Mode", "string", S("disabled"))
    p_raw = pub("/Fake/Debug/Raw", "int", N(0), {"cached": False})
    persistent = {"persistent": True}
    keep = [  # publishers must stay referenced or ntcore unpublishes them
        pub("/Fake/Tunable/Setpoint", "double", D(1.0), persistent),
        pub("/Fake/Tunable/Gain", "double", D(2.0), persistent),
        pub("/Fake/Tunable/Limit", "double", D(10.0), {"retained": True}),
    ]
    if args.run_id:
        keep.append(pub("/Fake/Evaluation/RunId", "string", S(args.run_id)))
    p_cmd = pub("/Fake/Output/Command", "double", D(2.0))
    keep += [  # the chooser widget: .type + options/default/active/selected
        pub("/Fake/Auto Chooser/.type", "string", S("String Chooser")),
        pub("/Fake/Auto Chooser/options", "string[]", B(["Left", "Center", "Right"])),
        pub("/Fake/Auto Chooser/default", "string", S("Center")),
        pub("/Fake/Auto Chooser/active", "string", S("")),
        pub("/Fake/Auto Chooser/selected", "string", S("")),
    ]
    # the robot's control loop reads Setpoint and Gain (and nothing reads Limit)
    sub_set = inst.getTopic("/Fake/Tunable/Setpoint").genericSubscribe()
    sub_gain = inst.getTopic("/Fake/Tunable/Gain").genericSubscribe()

    t0, n = time.monotonic(), 0
    print(f"fake robot listening on 127.0.0.1:{args.port}", flush=True)  # the first line: scripts wait for the word "listening"
    print(f"To use it, give the NT4 server these settings:\n  NT4_HOST = localhost\n  NT4_PORT = {args.port}\n"
          f"Leave this window open. Press Ctrl+C to stop the fake robot.", flush=True)
    while True:
        t = time.monotonic() - t0 + args.phase_seconds
        p_elev.set(D(elevator(t)))
        p_arm.set(D(arm(t)))
        p_mode.set(S(mode(t)))
        p_raw.set(N(n))
        sp, g = sub_set.get(), sub_gain.get()
        if sp.isValid() and g.isValid():
            p_cmd.set(D(sp.value() * g.value()))
        n += 1
        time.sleep(LOOP_PERIOD_S)

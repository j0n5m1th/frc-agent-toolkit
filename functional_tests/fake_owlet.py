"""A stand-in for CTRE's `owlet` command line, for the CTRE functional test. It is run by a tiny launcher the test installs as "owlet 26.3.0".

It behaves the way the real Owlet was seen to behave (docs/CTRE.md): it knows compliancy 19 only, `--scan` pads each name to one width,
progress bars are printed with carriage returns, a bad `-s` id prints `Invalid signal` and then exports EVERYTHING, and
FAKE_OWLET_MODE=fail makes it report success without writing a file. The output is a real .wpilog (built with the shared writer), so the log
server can open it.
"""

import os
import sys
from pathlib import Path

from frc_mcp_wpilib.wpilog_writer import LogWriter

OWN_LEVEL = 19
SIGNALS = (("RobotEnable", "ff0001"), ("DriveState/Pose", "ff0005"), ("Phoenix6/drive/TalonFX-0/Velocity", "10006fd"),
           ("Phoenix6/drive/TalonFX-0/Position", "10006fe"), ("Phoenix6/turret/TalonFX-5/Velocity", "10506fd"))


def log_level(hoot: Path) -> int:
    header = hoot.read_bytes()[:71]
    return header[70] if len(header) > 70 else 0


def write_log(target: Path, names: list[str]) -> None:
    writer = LogWriter()
    entries = {name: writer.start(name, "double") for name in names}
    for step in range(50):  # five seconds at 10 Hz
        for index, entry in enumerate(entries.values()):
            writer.append(entry, step * 0.5 + index, step * 100_000)
    writer.save(target)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: owlet <hoot_file> <out_file> [args]")
        return 1
    hoot = Path(argv[0]) if not argv[0].startswith("-") else Path(argv[1])
    flags = [a for a in argv if a.startswith("-")]
    if "--compliancy" in flags:
        print(f"owlet: {OWN_LEVEL}\nhoot log: {log_level(hoot)}")
        return 0
    if log_level(hoot) != OWN_LEVEL:
        print(f"Input file is an unsupported version: {log_level(hoot)}", file=sys.stderr)  # seen: exit code 1
        return 1
    if "--check-pro" in flags:
        print("hoot file is Pro-licensed")
        return 0
    if "--scan" in flags:
        for name, ident in SIGNALS:
            print(f"{name + ':':<74}{ident}")
        return 0
    target = Path(argv[1])
    ids = argv[argv.index("-s") + 1].split(",") if "-s" in argv else None
    print(f"Extracting {hoot}...")
    print("\rProgress: [  0%] [..........]\rProgress: [ 50%] [#####.....]\rProgress: [100%] [##########]", end="")
    if os.environ.get("FAKE_OWLET_MODE") == "fail":
        print("\nDataLog: Could not open log file, no log being saved")
        print(f"Output has been written to {target}")
        return 0  # seen: success reported, nothing written
    by_id = {ident: name for name, ident in SIGNALS}
    unknown = [i for i in ids or [] if i not in by_id]
    for bad in unknown:
        print(f"\nInvalid signal: {bad}")
    names = [name for name, _ in SIGNALS] if ids is None or unknown else [by_id[i] for i in ids]  # seen: a bad id exports everything
    write_log(target, names)
    print(f"\nOutput has been written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""A stand-in for CTRE's `caniv` command line, for the CTRE functional tests. It is run by a tiny launcher the test installs as "caniv 1.1.1.0".

Real, seen with a CANivore connected and without: the `--list` header and row layout, the tab-indented `--info` and `--license` lines (values here are made up), `--blink`
printing nothing, `Error value:-102` / `Error Message:"Could not open device"` on stdout with exit 127 for any action on a missing device, and `-206 CRF could not be read` for
a bad `--flash` file. Not real: what `--name`, `--reset` and `--flash` print on a device (they were never run on a real one); those are this file's own. Devices come from FAKE_CANIV_DEVICES (`serial:name:status:version,...`, default two) and every call is
appended to the file named by FAKE_CANIV_LOG, so a test can prove what ran and what did not.
"""

import os
import sys
from pathlib import Path

DEFAULT = "0123ABCD:robot-bus:Application:26.1.0.0,0456EF:spare:Application:25.4.0.0"


def devices() -> list[list[str]]:
    raw = os.environ.get("FAKE_CANIV_DEVICES", DEFAULT)
    return [row.split(":") for row in raw.split(",") if row]


def error(code: int, message: str, exit_code: int = 127) -> int:
    print(f"Error value:{code}\nError Message:\"{message}\"")
    return exit_code


def main(argv: list[str]) -> int:
    if log := os.environ.get("FAKE_CANIV_LOG"):
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(" ".join(argv) + "\n")
    if "--version" in argv:
        print("caniv 1.1.1.0")
        return 0
    if "--list" in argv:
        width = max([len("Serial Num"), *(len(row[0]) for row in devices())]) + 2  # caniv pads the first column to the longest serial (seen: 34 for a 32-digit serial)
        print(f"{'Serial Num':<{width}}{'Name':<20}{'Status':<20}{'Application Vers':<20}")
        for serial, name, status, version in devices():
            print(f"{serial:<{width}}{name:<20}{status:<20}{version:<20}")
        return 0
    if "--flash" in argv:
        path = Path(argv[argv.index("--flash") + 1])
        if not path.is_file() or not path.read_bytes():
            return error(-206, "CRF could not be read")
    wanted = argv[argv.index("-d") + 1] if "-d" in argv else None
    device = next((row for row in devices() if wanted is None or wanted in row[:2]), None)  # no -d means "any": the first one
    if device is None:
        return error(-102, "Could not open device")
    action = next((a for a in argv if a in ("--info", "--license", "--blink", "--reset", "--name", "--flash")), None)
    if action is None:
        return error(-2, "Invalid input", 1)
    if action == "--info":
        print(f"\tUSB Serial Number:            {device[0]}\n\tUSB Name:                     {device[1]}\n\tApplication Firmware:         26.1.0.0\n\tCAN Bus Termination:          true")
    elif action == "--license":
        print("\tNumber of Licenses:           1\n\tLicense 0:                    LIC-00-00000000-B-FRC")
    elif action != "--blink":  # blink prints nothing (seen)
        print(f"{action[2:]} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

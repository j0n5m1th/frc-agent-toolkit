"""A stand-in for CTRE's `passerine` command line, for the CTRE functional tests. It is run by a tiny launcher the test installs as "passerine 1.0.0".

It behaves the way the real one was seen to (passerine 1.0.0, from `--help` and real runs): `--input-midi` and `--output-chrp`, `Output has been written to <file>` on
success, `Error: invalid midi: not a midi file` with exit 1 for a file that is not MIDI, a missing folder is an error, and an existing output is silently overwritten.
FAKE_PASSERINE_MODE=silent makes it report success without writing a file.
"""

import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if "--version" in argv or "-V" in argv:
        print("passerine 1.0.0")
        return 0
    if "--input-midi" not in argv or "--output-chrp" not in argv:
        print("error: the following required arguments were not provided", file=sys.stderr)
        return 2
    source = Path(argv[argv.index("--input-midi") + 1])
    target = Path(argv[argv.index("--output-chrp") + 1])
    if not source.is_file():
        print("Error: The system cannot find the file specified. (os error 2)", file=sys.stderr)
        return 1
    if source.read_bytes()[:4] != b"MThd":
        print("Error: invalid midi: not a midi file", file=sys.stderr)
        return 1
    if os.environ.get("FAKE_PASSERINE_MODE") != "silent":
        if not target.parent.is_dir():
            print("Error: The system cannot find the path specified. (os error 3)", file=sys.stderr)
            return 1
        target.write_bytes(b"\x00\x00\x00\x01" * 15)  # overwrites, like the real one
    print(f"Output has been written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

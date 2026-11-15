"""A deterministic fake robot for the evaluations (answers must not depend on when a question is asked).

The code lives in the NT4 server package (`frc_mcp_nt4.demo`, also `frc-mcp demo robot`); this script is a thin wrapper that keeps the
evaluation port, 5820, and the plain-script imports (`from fake_robot import ...`) working.

    uv run --project plugins/frc python evaluations/fake_robot.py [--port 5820]

`frc_mcp_nt4/demo.py` documents every signal.
"""

from collections.abc import Sequence

import frc_mcp_nt4.demo as demo
from frc_mcp_nt4.demo import LOOP_PERIOD_S, run
from frc_mcp_nt4.demo import build_parser as _build_parser
from frc_mcp_wpilib.practice_signals import (
    ARM_HI,
    ARM_LO,
    ARM_PERIOD,
    BASE_A,
    ELEVATOR_PERIOD,
    MODES,
    SPIKE_A,
    SPIKE_S,
    arm,
    elevator,
    mode,
)

DEFAULT_PORT = 5820  # the evaluations' port; `frc-mcp demo robot` uses 5810


def build_parser():
    return _build_parser(DEFAULT_PORT)


def parse_args(argv: Sequence[str] | None = None):
    return demo.parse_args(argv, DEFAULT_PORT)


def main(argv: Sequence[str] | None = None) -> int:
    return demo.main(argv, DEFAULT_PORT)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ARM_HI", "ARM_LO", "ARM_PERIOD", "BASE_A", "DEFAULT_PORT", "ELEVATOR_PERIOD", "LOOP_PERIOD_S", "MODES", "SPIKE_A", "SPIKE_S",
           "arm", "build_parser", "elevator", "main", "mode", "parse_args", "run"]

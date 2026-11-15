"""The made-up signals the practice robot (`frc-mcp demo robot`) and the practice log (`frc-mcp demo log`) share, so the two always agree.

Every signal is a function of `t`, the seconds since the start, and repeats forever.
"""

ELEVATOR_PERIOD, SPIKE_S, SPIKE_A, BASE_A = 5.0, 0.4, 42.5, 5.0
ARM_PERIOD, ARM_LO, ARM_HI = 10.0, -30.0, 90.0
MODES = (("disabled", 3.0), ("auto", 2.0), ("teleop", 4.0))  # 9 s cycle


def elevator(t: float) -> float:
    """5.0 A, except 42.5 A during the first 0.4 s of every 5 s."""
    return SPIKE_A if (t % ELEVATOR_PERIOD) < SPIKE_S else BASE_A


def arm(t: float) -> float:
    """A triangle wave from -30 to 90 degrees, period 10 s (rising -30 to 90 over 0 to 5 s)."""
    half = ARM_PERIOD / 2
    p = t % ARM_PERIOD
    frac = p / half if p < half else 2 - p / half  # 0..1..0
    return ARM_LO + (ARM_HI - ARM_LO) * frac


def mode(t: float) -> str:
    """The 9 s cycle: disabled [0, 3), auto [3, 5), teleop [5, 9)."""
    p = t % sum(d for _, d in MODES)
    for name, d in MODES:
        if p < d:
            return name
        p -= d
    return MODES[-1][0]

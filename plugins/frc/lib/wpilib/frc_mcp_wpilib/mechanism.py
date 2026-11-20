"""Mechanism tracking, shared by the log server and the live server: how closely one mechanism (an arm, an elevator, a flywheel) followed its setpoint.

The caller names the mechanism's signals; nothing is guessed from a name, because nothing in a log says that a signal is a setpoint or a measurement, or
what its units are. A setpoint in rotations beside a position in meters gives a following error that means nothing, so the two must share units.

Following error is the measurement minus the setpoint in force. A step is a setpoint change larger than 5% of the previous setpoint (and at least 0.01);
its overshoot and settling time (until the measurement stays within 5% of the step size) are rules of thumb, not a controller analysis. A stall is a stretch
with the mechanism nearly stopped while its current is high.
"""

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from typing import Any

from .reduce.stats import US_PER_S
from .scan_rows import Row, Window, everywhere, finite_numbers, value_at, within
from .tracking import (
    Samples,
    held_pairs,
    require_enough,
    seconds_since,
)

STEP_FRACTION = 0.05  # a setpoint change this big (of the previous setpoint) is a step
STEP_MIN = 0.01
SETTLE_BAND = 0.05  # settled = staying within 5% of the step size around the new setpoint
STEP_LOOKAHEAD_US = 5 * US_PER_S  # a step is judged on the measurement for this long, or until the setpoint changes again
STEP_MIN_SAMPLES = 5
STALL_AMPS = 30.0
STALL_SPEED = 0.01  # |velocity| below this counts as stopped, in the velocity signal's units
MAX_STALLS_LISTED = 3


def _following_error(measurement: Samples, setpoint: Samples, windows: Sequence[Window], origin_us: int) -> dict[str, Any]:
    errors: list[tuple[int, float]] = []
    for t, m, _, c in held_pairs(finite_numbers(measurement), finite_numbers(setpoint), windows):
        errors.append((t, float(m) - float(c)))
    require_enough(len(errors))
    values = [e for _, e in errors]
    worst = max(errors, key=lambda r: abs(r[1]))
    return {"n": len(values), "rmse": round(math.sqrt(sum(e * e for e in values) / len(values)), 4), "bias": round(sum(values) / len(values), 4),
            "max_abs": round(abs(worst[1]), 4), "max_at_s": seconds_since(worst[0], origin_us)}


def _commands(setpoint: Sequence[Row], window: Window) -> list[Row]:
    """The setpoint changes inside one window, led by the setpoint that was already in force when the window opened."""
    lo, hi = window
    held = value_at(setpoint, lo)
    return ([(lo, held)] if held is not None else []) + [r for r in setpoint if lo < r[0] <= hi]


def _steps(setpoint: Sequence[Row], measurement: Sequence[Row], windows: Sequence[Window]) -> dict[str, Any] | None:
    numbers = finite_numbers(setpoint)
    times = [t for t, _ in measurement]
    overshoots: list[float] = []
    settles: list[float] = []
    found = 0
    for window in windows:
        commands = _commands(numbers, window)
        for i in range(1, len(commands)):
            (_, before), (t, after) = commands[i - 1], commands[i]
            size = abs(float(after) - float(before))
            if size <= max(STEP_MIN, STEP_FRACTION * abs(float(before))):
                continue
            end = min(t + STEP_LOOKAHEAD_US, commands[i + 1][0] if i + 1 < len(commands) else t + STEP_LOOKAHEAD_US)
            seen = measurement[bisect_left(times, t):bisect_right(times, end)]
            if len(seen) < STEP_MIN_SAMPLES:
                continue
            found += 1
            direction = 1 if float(after) > float(before) else -1
            overshoots.append(max(0.0, max(direction * (float(v) - float(after)) for _, v in seen) / size * 100))
            outside = [at for at, v in seen if abs(float(v) - float(after)) > SETTLE_BAND * size]
            if not outside or outside[-1] < seen[-1][0]:  # the measurement ended inside the band: it settled
                settles.append((outside[-1] - t) / US_PER_S if outside else 0.0)
    if not found:
        return None
    out: dict[str, Any] = {"count": found, "settled": len(settles), "overshoot_pct": {"avg": round(sum(overshoots) / found, 1), "max": round(max(overshoots), 1)}}
    if settles:
        out["settling_s"] = {"avg": round(sum(settles) / len(settles), 2), "max": round(max(settles), 2)}
    return out


def _stalls(velocity: Sequence[Row], current: Sequence[Row], windows: Sequence[Window], origin_us: int) -> dict[str, Any]:
    rows = within(finite_numbers(velocity), windows)
    amps = finite_numbers(current)
    stretches: list[list[Any]] = []  # start, end, max amps
    for i, (t, v) in enumerate(rows):
        end = rows[i + 1][0] if i + 1 < len(rows) else t
        a = value_at(amps, t)
        if abs(float(v)) < STALL_SPEED and a is not None and abs(float(a)) > STALL_AMPS:
            if stretches and stretches[-1][1] == t:
                stretches[-1][1] = end
                stretches[-1][2] = max(stretches[-1][2], abs(float(a)))
            else:
                stretches.append([t, end, abs(float(a))])
    amp_times = [t for t, _ in amps]
    for stretch in stretches:  # the peak is the highest current anywhere in the stretch, not only at velocity updates
        inside = amps[bisect_left(amp_times, stretch[0]):bisect_right(amp_times, stretch[1])]
        stretch[2] = max([stretch[2], *(abs(float(a)) for _, a in inside)])
    out: dict[str, Any] = {"count": len(stretches), "total_s": round(sum(e - s for s, e, _ in stretches) / US_PER_S, 1)}
    if stretches:
        out["first"] = [{"start_s": seconds_since(s, origin_us), "end_s": seconds_since(e, origin_us), "max_a": round(a, 1)} for s, e, a in stretches[:MAX_STALLS_LISTED]]
    return out


def analyze_mechanism(setpoint: Samples, measurement: Samples, windows: Sequence[Window], origin_us: int, velocity: Samples | None = None,
                      current: Samples | None = None, temperature: Samples | None = None) -> dict[str, Any]:
    """How closely the measurement followed the setpoint, its steps, its stalls (with velocity and current) and its temperature (all over the windows).
    Raises NothingToCompare when the setpoint and measurement cannot be compared."""
    windows = everywhere(windows)
    measured = finite_numbers(measurement)
    out: dict[str, Any] = {"following_error": _following_error(measurement, setpoint, windows, origin_us)}
    if steps := _steps(setpoint, within(measured, windows), windows):
        out["steps"] = steps
    skipped = []
    if velocity is not None and current is not None:
        out["stalls"] = _stalls(velocity, current, windows, origin_us)
    elif velocity is not None or current is not None:
        skipped.append("stalls need both velocity and current")
    if temperature is not None:
        hot = within(finite_numbers(temperature), windows)
        if hot:
            peak = max(hot, key=lambda r: float(r[1]))
            out["temperature"] = {"max": round(float(peak[1]), 1), "max_at_s": seconds_since(peak[0], origin_us), "last": round(float(hot[-1][1]), 1)}
    if skipped:
        out["skipped"] = skipped
    return out

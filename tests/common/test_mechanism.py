"""The shared mechanism check: following error, steps with overshoot and settling, stalls, temperature."""

import pytest

from frc_mcp_wpilib.mechanism import analyze_mechanism
from frc_mcp_wpilib.tracking import NothingToCompare

S = 1_000_000
STEP_US = 20_000


def response(before, after, delta_s):
    """Rises 110% of the way in 0.3 s, comes back to 100% by 0.6 s and stays."""
    size = after - before
    if delta_s < 0.3:
        return before + size * 1.1 * delta_s / 0.3
    if delta_s < 0.6:
        return before + size * (1.1 - 0.1 * (delta_s - 0.3) / 0.3)
    return after


def series(steps, seconds=10):
    """`steps` is `[(time_s, new setpoint)]`, starting from 0. Returns (setpoint rows, measurement rows) at 50 Hz."""
    setpoint = [(0, 0.0)] + [(round(t * S), v) for t, v in steps]
    measurement = []
    for n in range(seconds * 50):
        t = n * 0.02
        before, after, start = 0.0, 0.0, 0.0
        for at, v in steps:
            if t >= at:
                before, after, start = after, v, at
        measurement.append((n * STEP_US, response(before, after, t - start) if after != before else after))
    return setpoint, measurement


def test_following_error_steps_overshoot_and_settling_are_measured():
    setpoint, measurement = series([(2.0, 1.0), (6.0, 0.2)])
    out = analyze_mechanism(setpoint, measurement, [(0, 10 * S)], 0)
    fe = out["following_error"]
    assert fe["n"] == 500 and fe["rmse"] > 0 and fe["max_abs"] > 0.5
    assert out["steps"]["count"] == 2 and out["steps"]["settled"] == 2
    assert out["steps"]["overshoot_pct"]["max"] == pytest.approx(10.0, abs=1.5)
    assert 0.38 <= out["steps"]["settling_s"]["max"] <= 0.52


def test_a_mechanism_that_never_settles_is_not_called_settled():
    setpoint = [(0, 0.0), (S, 1.0)]
    measurement = [(n * STEP_US, 0.0 if n * STEP_US < S else 1.0 + (0.5 if n % 2 else -0.5)) for n in range(150)]  # rings at 50% forever
    out = analyze_mechanism(setpoint, measurement, [(0, 3 * S)], 0)
    assert out["steps"]["count"] == 1 and out["steps"]["settled"] == 0 and "settling_s" not in out["steps"]


def test_a_stopped_mechanism_with_high_current_is_a_stall_and_a_moving_one_is_not():
    setpoint, measurement = series([(2.0, 1.0)], seconds=4)
    velocity = [(n * STEP_US, 0.0 if 100 <= n < 150 else 0.5) for n in range(200)]
    current = [(n * STEP_US, 45.0 if 100 <= n < 150 else 10.0) for n in range(200)]
    out = analyze_mechanism(setpoint, measurement, [(0, 4 * S)], 0, velocity, current)
    assert out["stalls"]["count"] == 1 and out["stalls"]["total_s"] == pytest.approx(1.0, abs=0.05) and out["stalls"]["first"][0]["max_a"] == 45.0
    cool = analyze_mechanism(setpoint, measurement, [(0, 4 * S)], 0, velocity, [(n * STEP_US, 10.0) for n in range(200)])
    assert cool["stalls"]["count"] == 0


def test_velocity_without_current_says_stalls_were_skipped_and_temperature_is_reported():
    setpoint, measurement = series([(2.0, 1.0)], seconds=4)
    temperature = [(n * STEP_US, 30.0 + n * 0.1) for n in range(200)]
    out = analyze_mechanism(setpoint, measurement, [(0, 4 * S)], 0, velocity=[(0, 0.0)], temperature=temperature)
    assert out["skipped"] == ["stalls need both velocity and current"] and out["temperature"]["max"] == pytest.approx(49.9, abs=0.1)


def test_too_few_comparisons_are_nothing_to_compare():
    with pytest.raises(NothingToCompare, match="at least 10"):
        analyze_mechanism([(0, 1.0)], [(n * STEP_US, 1.0) for n in range(3)], [(0, S)], 0)
    with pytest.raises(NothingToCompare):
        analyze_mechanism([], [(0, 1.0)], [(0, S)], 0)

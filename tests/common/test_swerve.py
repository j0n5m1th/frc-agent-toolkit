"""The swerve comparison shared by the log and live servers: naming conventions, decoding, windows, and the lag rule."""

import math
import struct

import pytest

from frc_mcp_wpilib.swerve import (
    STATE_TYPE,
    NothingToCompare,
    analyze,
    decode_states,
    lagging,
    pick_pair,
)

S = 1_000_000


def raw(*modules):
    return b"".join(struct.pack("<dd", speed, angle) for speed, angle in modules)


def feed(count, measured_of, step_us=20_000, start=0):
    """Matching measured and commanded samples; the command is 2 m/s at 0.5 rad on four modules."""
    command = [(start + n * step_us, raw(*[(2.0, 0.5)] * 4)) for n in range(count)]
    measured = [(t + 1000, raw(*measured_of(n))) for n, (t, _) in enumerate(command)]
    return measured, command


def test_a_convention_pair_is_found_and_other_swerve_arrays_are_named():
    types = {"/RealOutputs/SwerveStates/Measured": STATE_TYPE, "/RealOutputs/SwerveStates/SetpointsOptimized": STATE_TYPE,
             "/RealOutputs/SwerveStates/Setpoints": STATE_TYPE, "/Other/Array": STATE_TYPE, "/Not/Swerve": "double[]"}
    pair = pick_pair(types)
    assert (pair.measured, pair.setpoint, pair.how) == ("/RealOutputs/SwerveStates/Measured", "/RealOutputs/SwerveStates/SetpointsOptimized", "convention")
    assert pair.others == ["/Other/Array", "/RealOutputs/SwerveStates/Setpoints"]


def test_the_unoptimized_setpoints_are_used_when_there_are_no_optimized_ones():
    pair = pick_pair({"/SwerveStates/Measured": STATE_TYPE, "/SwerveStates/Setpoints": STATE_TYPE})
    assert pair.setpoint == "/SwerveStates/Setpoints"


def test_a_suggestive_name_alone_is_never_picked():
    pair = pick_pair({"/Drive/ModuleStates": STATE_TYPE, "/Drive/Targets": STATE_TYPE})
    assert pair.measured is None and pair.how == "none" and pair.others == ["/Drive/ModuleStates", "/Drive/Targets"]


def test_named_topics_must_be_swerve_arrays():
    pair = pick_pair({"/A": STATE_TYPE, "/B": "double[]"}, "/A", "/B")
    assert pair.measured == "/A" and pair.setpoint is None and pair.how == "explicit"


def test_decoding_needs_whole_module_states():
    assert decode_states(raw((1.5, 0.25), (2.0, -0.5)))[1].angle == -0.5
    for bad in (b"", b"\x00" * 15, "text", None):
        with pytest.raises(NothingToCompare):
            decode_states(bad)


def test_a_lagging_module_gets_the_larger_error_and_only_the_window_counts():
    measured, command = feed(200, lambda n: [(1.95, 0.5)] * 3 + [(1.0, 0.5 + math.radians(30))])
    out = analyze(measured, command, [(1 * S, 3 * S)], origin_us=0)
    by = {m["module"]: m for m in out["modules"]}
    assert by[3]["speed_err_p95_mps"] == pytest.approx(1.0, abs=0.01) and by[3]["angle_err_p95_deg"] == pytest.approx(30.0, abs=0.1)
    assert by[0]["speed_err_p95_mps"] == pytest.approx(0.05, abs=0.01) and out["worst_speed_err"]["module"] == 3
    assert min(m["n"] for m in out["modules"]) == 100  # 1 s to 3 s at 50 Hz, not all 200 updates


def test_no_commanded_update_before_the_measured_ones_is_nothing_to_compare():
    measured = [(t, raw(*[(1.0, 0.0)] * 4)) for t in range(0, 40_000, 2000)]
    with pytest.raises(NothingToCompare, match="commanded one before"):
        analyze(measured, [(10 * S, raw(*[(1.0, 0.0)] * 4))], [(0, 20 * S)], 0)


def test_too_few_updates_are_nothing_to_compare():
    measured, command = feed(5, lambda n: [(2.0, 0.5)] * 4)
    with pytest.raises(NothingToCompare, match="at least 10"):
        analyze(measured, command, [(0, S)], 0)


def test_every_update_counts_so_a_one_update_worst_moment_is_not_thinned_away():
    measured, command = feed(30_000, lambda n: [(1.9 if n != 12_345 else 0.5, 0.5)] * 4)
    out = analyze(measured, command, [(0, 10**12)], 0)
    assert out["modules"][0]["n"] == 30_000 and out["worst_speed_err"]["mps"] == 1.5 and out["worst_speed_err"]["at_s"] == 246.9


def test_the_lag_rule_needs_three_modules_a_big_error_and_twice_the_typical():
    def modules(*speed_errors):
        return [{"module": k, "speed_err_p95_mps": e} for k, e in enumerate(speed_errors)]

    assert lagging(modules(0.05, 0.05, 0.05, 1.0)) and "module 3" in lagging(modules(0.05, 0.05, 0.05, 1.0))[0]
    assert lagging(modules(0.05, 0.05, 0.05, 0.2)) == []  # twice the typical but under the floor
    assert lagging(modules(0.5, 0.5, 0.5, 0.6)) == []  # above the floor but not twice the typical
    assert lagging(modules(0.05, 1.0)) == []  # two modules: no typical one

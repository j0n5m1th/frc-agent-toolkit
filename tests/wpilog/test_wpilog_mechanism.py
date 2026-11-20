"""wpilog_mechanism: the signals are named by the caller, and following error, steps, stalls and temperature come back."""

import json

import pytest
from log_builder import BASE, S

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog.tools import data_tools, file_tools

SETPOINT, POSITION = "/RealOutputs/Elevator/GoalMeters", "/Elevator/PositionMeters"


def text(result):
    return json.loads(result.content[0].text)


def at(seconds):
    return BASE + int(seconds * S)


def build(path):
    """Enabled 1-9 s; the setpoint steps 0 -> 1 at 2 s; the elevator overshoots 10% and settles; it stalls (stopped, 45 A) from 6.5 to 7.5 s."""
    w = LogWriter()
    enabled = w.start("DS:enabled", "boolean", "", BASE)
    for t, on in ((0, False), (1, True), (9, False)):
        w.append(enabled, on, at(t))
    goal, pos, vel, amps, temp = (w.start(n, "double", "", BASE) for n in (SETPOINT, POSITION, "/Elevator/Velocity", "/Elevator/CurrentAmps", "/Elevator/TempC"))
    w.append(goal, 0.0, BASE)
    w.append(goal, 1.0, at(2.0))
    for n in range(500):
        t = n * 0.02
        d = t - 2.0
        m = 0.0 if d < 0 else 1.1 * d / 0.3 if d < 0.3 else 1.1 - 0.1 * (d - 0.3) / 0.3 if d < 0.6 else 1.0
        stall = 6.5 <= t < 7.5
        w.append(pos, m, at(t))
        w.append(vel, 0.0 if stall else 0.4, at(t))
        w.append(amps, 45.0 if stall else 12.0, at(t))
        w.append(temp, 30.0 + t, at(t))
    w.save(path)
    return path.name


@pytest.fixture
def elevator_log(log_dir):
    return build(log_dir / "elevator.wpilog")


async def test_a_mechanism_is_judged_from_the_signals_the_caller_names(elevator_log):
    await file_tools.wpilog_open(elevator_log)
    out = text(await data_tools.wpilog_mechanism(setpoint=SETPOINT, measurement=POSITION, velocity="/Elevator/Velocity", current="/Elevator/CurrentAmps", temperature="/Elevator/TempC"))
    assert out["scope"] == "enabled" and out["roles"]["setpoint"] == SETPOINT
    assert out["steps"]["count"] == 1 and out["steps"]["overshoot_pct"]["max"] == pytest.approx(10.0, abs=1.5)
    assert out["stalls"]["count"] == 1 and out["stalls"]["first"][0]["max_a"] == 45.0
    assert out["temperature"]["max"] > 30.0 and out["following_error"]["n"] > 300


async def test_without_velocity_and_current_there_are_no_stalls_and_it_says_so(elevator_log):
    await file_tools.wpilog_open(elevator_log)
    out = text(await data_tools.wpilog_mechanism(setpoint=SETPOINT, measurement=POSITION, velocity="/Elevator/Velocity"))
    assert "stalls" not in out and out["skipped"] == ["stalls need both velocity and current"]


async def test_a_missing_or_non_number_signal_is_an_error(elevator_log):
    await file_tools.wpilog_open(elevator_log)
    with pytest.raises(Exception, match="not in this log"):
        await data_tools.wpilog_mechanism(setpoint="/No/Such", measurement=POSITION)
    with pytest.raises(Exception, match="must be a number"):
        await data_tools.wpilog_mechanism(setpoint="DS:enabled", measurement=POSITION)


async def test_a_window_with_too_few_updates_says_nothing_could_be_compared(elevator_log):
    await file_tools.wpilog_open(elevator_log)
    out = text(await data_tools.wpilog_mechanism(setpoint=SETPOINT, measurement=POSITION, since_ms=0, until_ms=100))
    assert out["status"] == "no_match" and "at least 10" in out["reason"]

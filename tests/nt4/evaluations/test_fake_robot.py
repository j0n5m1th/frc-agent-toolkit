"""Pure-logic tests for evaluations/fake_robot.py (no server is started)."""

import fake_robot
import pytest


@pytest.mark.parametrize("t, expected", [
    (0.0, fake_robot.SPIKE_A), (0.39, fake_robot.SPIKE_A), (0.4, fake_robot.BASE_A),
    (4.99, fake_robot.BASE_A), (5.0, fake_robot.SPIKE_A), (12.5, fake_robot.BASE_A),
])
def test_elevator_spikes_at_the_start_of_every_period(t, expected):
    assert fake_robot.elevator(t) == expected


def test_arm_is_a_triangle_wave_between_its_limits():
    assert fake_robot.arm(0) == fake_robot.ARM_LO
    assert fake_robot.arm(5) == fake_robot.ARM_HI
    assert fake_robot.arm(10) == fake_robot.ARM_LO
    assert fake_robot.arm(2.5) == pytest.approx(30.0)
    assert fake_robot.arm(7.5) == pytest.approx(30.0)
    assert fake_robot.arm(3.0) == fake_robot.arm(10 + 3.0)


@pytest.mark.parametrize("t, expected", [
    (0, "disabled"), (2.99, "disabled"), (3, "auto"), (4.99, "auto"), (5, "teleop"), (8.99, "teleop"),
    (9, "disabled"), (9 + 3, "auto"),
])
def test_mode_cycles_every_nine_seconds(t, expected):
    assert fake_robot.mode(t) == expected


def test_mode_cycle_length_matches_the_table():
    assert sum(d for _, d in fake_robot.MODES) == 9.0


def test_parse_args_defaults():
    args = fake_robot.parse_args([])
    assert args.port == fake_robot.DEFAULT_PORT
    assert args.phase_seconds == 0.0 and args.run_id is None and args.persistence_file is None


def test_parse_args_accepts_overrides(tmp_path):
    path = tmp_path / "nt.json"
    args = fake_robot.parse_args(["--port", "5999", "--phase-seconds", "2.5", "--run-id", "abc",
                                  "--persistence-file", str(path)])
    assert (args.port, args.phase_seconds, args.run_id, args.persistence_file) == (5999, 2.5, "abc", path)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_parse_args_rejects_non_finite_phase(value, capsys):
    with pytest.raises(SystemExit) as exc:
        fake_robot.parse_args([f"--phase-seconds={value}"])
    assert exc.value.code == 2 and "finite" in capsys.readouterr().err

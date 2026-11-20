"""The shared correlation and pose checks."""

import math
import struct

import pytest

from frc_mcp_wpilib.correlate import analyze_correlation
from frc_mcp_wpilib.pose import POSE_TYPE, analyze_pose, decode_pose, pick_pose
from frc_mcp_wpilib.tracking import NothingToCompare

S = 1_000_000
STEP = 20_000


def wave(delay_s=0.0, scale=1.0, n=500):
    return [(i * STEP, scale * math.sin((i * STEP / S - delay_s) * 2.0)) for i in range(n)]


def test_a_signal_that_follows_another_shows_the_lag_it_matches_best():
    out = analyze_correlation(wave(), wave(delay_s=0.5), [], max_lag_s=0.8)
    assert out["best_lag"]["lag_s"] == pytest.approx(0.5, abs=0.06) and out["best_lag"]["r"] > 0.97 and out["best_lag"]["meaning"] == "b follows a"
    assert "Moving together is not one causing the other" in out["note"]


def test_the_same_signal_is_strongly_together_and_the_negative_one_is_opposite():
    out = analyze_correlation(wave(), wave(), [], max_lag_s=0)
    assert out["r"] == 1.0 and out["strength"] == "strong" and out["direction"] == "together" and "best_lag" not in out
    assert analyze_correlation(wave(), wave(scale=-1.0), [], max_lag_s=0)["direction"] == "opposite"


def test_a_signal_that_never_changes_or_too_little_overlap_is_nothing_to_correlate():
    with pytest.raises(NothingToCompare, match="never changes"):
        analyze_correlation(wave(), [(i * STEP, 3.0) for i in range(500)], [], max_lag_s=0)
    with pytest.raises(NothingToCompare, match="no readings"):
        analyze_correlation(wave(), [], [], max_lag_s=0)
    with pytest.raises(NothingToCompare, match="do not overlap"):
        analyze_correlation(wave(n=100), [(10 * S + i * STEP, float(i)) for i in range(100)], [], max_lag_s=0)


def pose_rows(count=250, jump_at=None, offset=0.0):
    out = []
    for i in range(count):
        x = i * 0.04 + (1.0 if jump_at is not None and i >= jump_at else 0.0) + offset
        out.append((i * STEP, struct.pack("<ddd", x, 1.0, 0.1)))
    return out


def test_a_pose_that_moves_faster_than_a_robot_is_a_jump_and_a_normal_drive_is_not():
    assert analyze_pose(pose_rows(), [], 0)["jumps"] == {"count": 0}
    out = analyze_pose(pose_rows(jump_at=100), [], 0)
    assert out["jumps"]["count"] == 1 and out["jumps"]["largest"][0] == {"at_s": 2.0, "meters": 1.04, "degrees": 0.0}


def test_two_poses_are_compared_in_position_and_heading():
    reference = [(t, struct.pack("<ddd", struct.unpack("<ddd", raw)[0] + 0.3, 1.0, 0.1 + math.radians(4))) for t, raw in pose_rows()]
    out = analyze_pose(pose_rows(), [], 0, reference)["vs_reference"]
    assert out["meters"]["mean"] == pytest.approx(0.3, abs=0.001) and out["degrees"]["max"] == pytest.approx(4.0, abs=0.1) and out["n"] == 250


def test_a_robot_pose_is_found_by_convention_and_other_poses_are_only_listed():
    types = {"/DriveState/Pose": POSE_TYPE, "/Vision/Pose": POSE_TYPE, "/Drive/Setpoint": "double"}
    pick = pick_pose(types)
    assert (pick.pose, pick.how, pick.others) == ("/DriveState/Pose", "convention", ["/Vision/Pose"])
    assert pick_pose({"/Vision/Pose": POSE_TYPE}).pose is None and pick_pose(types, "/Vision/Pose").how == "explicit"
    assert pick_pose(types, "/Drive/Setpoint").pose is None  # not a Pose2d


def test_bytes_that_are_not_a_pose_are_refused_and_too_few_updates_are_nothing_to_check():
    with pytest.raises(NothingToCompare):
        decode_pose(bytes(23))
    with pytest.raises(NothingToCompare, match="fewer than 10"):
        analyze_pose(pose_rows(count=3), [], 0)

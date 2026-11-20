"""The shared power check and the robot-info map: conventions, names only from the robot's own map, and the brownout setting."""

import json

import pytest

from frc_mcp_wpilib.power import (
    BROWNOUT_DEFAULT_V,
    analyze_power,
    brownout_threshold,
    current_sources,
    expand,
    threshold_topic,
)
from frc_mcp_wpilib.robot_info import info_topics, parse_map
from frc_mcp_wpilib.scan_roles import ScanTopic
from frc_mcp_wpilib.tracking import NothingToCompare

S = 1_000_000


def topic(name, type_str="double"):
    return ScanTopic(name, type_str)


def test_current_signals_are_found_by_convention_and_look_alikes_are_only_listed():
    visible = [topic("/PowerDistribution/ChannelCurrent", "double[]"), topic("/Elevator/StatorCurrent"), topic("/PowerDistribution[0]/Chan7"), topic("/Arm/CurrentDraw"),
               topic("/Elevator/CurrentLimit")]
    found = current_sources(visible)
    assert [t.name for t in found.topics] == ["/PowerDistribution/ChannelCurrent", "/Elevator/StatorCurrent", "/PowerDistribution[0]/Chan7"] and found.by == "convention"
    assert found.candidates == []  # something was found, so look-alikes are not even listed
    only_odd = current_sources([topic("/Arm/CurrentDraw"), topic("/Elevator/CurrentLimit")])
    assert only_odd.topics == [] and only_odd.candidates == ["/Arm/CurrentDraw", "/Elevator/CurrentLimit"]


def test_named_current_signals_are_used_as_given():
    found = current_sources([topic("/Arm/CurrentDraw")], ["/Arm/CurrentDraw"])
    assert found.by == "explicit" and [t.name for t in found.topics] == ["/Arm/CurrentDraw"]


def test_an_array_becomes_one_series_per_channel_and_a_chan_signal_is_its_channel():
    rows = [(0, [1.0, 2.0, 3.0]), (S, [4.0, 5.0, 6.0])]
    series = expand(topic("/PowerDistribution/ChannelCurrent", "double[]"), rows)
    assert [(s.channel, [v for _, v in s.rows]) for s in series] == [(0, [1.0, 4.0]), (1, [2.0, 5.0]), (2, [3.0, 6.0])]
    assert expand(topic("/PowerDistribution[0]/Chan7"), [(0, 1.0)])[0].channel == 7
    assert expand(topic("/Elevator/StatorCurrent"), [(0, 1.0)])[0].channel is None


def test_the_robots_map_is_read_from_json_and_bad_updates_are_skipped():
    assert parse_map([(0, "not json"), (1, json.dumps({"3": "Elevator", "x": "ignored", "12": "Intake"}))]) == {3: "Elevator", 12: "Intake"}
    assert parse_map([(0, json.dumps({"3": "Old"})), (1, "{broken")]) == {3: "Old"}  # the last update that parses
    assert parse_map([]) == {} and parse_map([(0, 5)]) == {}
    assert parse_map([(0, json.dumps({"1": "x" * 100}))])[1] == "x" * 40


def test_robot_info_topics_are_text_under_a_robotinfo_folder():
    topics = [topic("/RobotInfo/PdhChannels", "string"), topic("/RealOutputs/RobotInfo/CanDevices", "string"), topic("/Other/PdhChannels", "string"), topic("/RobotInfo/PdhChannels2", "string")]
    assert info_topics(topics) == {"pdh": "/RobotInfo/PdhChannels", "can": "/RealOutputs/RobotInfo/CanDevices"}


def test_the_brownout_setting_is_given_then_logged_then_the_nominal_default():
    setting = topic("/SystemStats/BrownoutVoltage")
    assert threshold_topic([topic("/A"), setting]) == setting
    assert brownout_threshold(None, [], 5.0) == (5.0, "given")
    assert brownout_threshold(setting, [(0, 6.3), (S, 6.4)]) == (6.4, "logged (/SystemStats/BrownoutVoltage)")
    volts, basis = brownout_threshold(None, [])
    assert volts == BROWNOUT_DEFAULT_V == 6.0 and basis.startswith("default")


def test_peaks_are_ranked_and_each_device_is_shown_with_what_it_drew_at_the_low_point():
    voltage = [(n * S, 12.0 if n != 5 else 5.0) for n in range(10)]
    series = expand(topic("/PowerDistribution/ChannelCurrent", "double[]"), [(n * S, [10.0, 80.0 if n == 5 else 2.0, 30.0]) for n in range(10)])
    out = analyze_power(voltage, [], series, (6.0, "default"), [(0, 9 * S)], 0, {1: "Elevator"})
    assert [(r["channel"], r["peak_a"]) for r in out["top_current"]] == [(1, 80.0), (2, 30.0), (0, 10.0)]
    assert out["top_current"][0]["name"] == "Elevator" and out["top_current"][0]["battery_v_then"] == 5.0 and out["top_current"][1].get("name") is None
    assert out["voltage"]["min_v"] == 5.0 and out["voltage"]["min_at_s"] == 5.0 and out["at_lowest_voltage"][0] == {"source": "/PowerDistribution/ChannelCurrent", "channel": 1, "name": "Elevator", "amps": 80.0}


def test_nothing_to_check_is_said_and_nan_is_ignored():
    with pytest.raises(NothingToCompare):
        analyze_power([], [], [], (6.0, "default"), [], 0)
    out = analyze_power([(0, 12.0), (S, float("nan")), (2 * S, 11.0)], [], [], (6.0, "default"), [], 0)
    assert out["voltage"]["min_v"] == 11.0


def test_a_named_hidden_current_topic_is_used_and_time_below_brownout_does_not_cross_a_disabled_gap():
    hidden = topic("/Hidden/Amps")
    found = current_sources([topic("/Visible/Amps")], ["/Hidden/Amps"], {"/Hidden/Amps": hidden, "/Visible/Amps": topic("/Visible/Amps")})
    assert [t.name for t in found.topics] == ["/Hidden/Amps"]
    # 5.5 V at the end of auto, then 12 V at the start of teleop 90 s later: only the time inside a window counts
    voltage = [(8 * S, 5.5), (9 * S, 5.5), (99 * S, 12.0), (100 * S, 12.0)]
    out = analyze_power(voltage, [], [], (6.0, "default"), [(0, 9 * S), (99 * S, 100 * S)], 0)
    assert out["voltage"]["below_brownout_s"] == 1.0


def test_a_brownout_flag_still_true_when_the_window_ends_is_cut_at_the_end_of_the_window():
    flag = [(0, False), (int(8.9 * S), True)]
    out = analyze_power([], [flag], [], (6.0, "default"), [(0, 9 * S)], 0)
    assert out["brownouts"]["count"] == 1 and out["brownouts"]["total_s"] == 0.1

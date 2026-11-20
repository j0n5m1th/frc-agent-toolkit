"""wpilog_power: which device drew the current when the battery sagged, with names only from the robot's own map."""

import json

import pytest
from log_builder import BASE, S

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog.tools import data_tools, file_tools

BATTERY = "/SystemStats/BatteryVoltage"
CHANNELS = "/RealOutputs/PowerDistribution/ChannelCurrent"
INFO = "/RealOutputs/RobotInfo/PdhChannels"


def text(result):
    return json.loads(result.content[0].text)


def at(seconds):
    return BASE + int(seconds * S)


def build(path, *, info=True, brownout=True):
    """Enabled 1-9 s. The battery sags to 5.5 V at 4.0-4.2 s while channel 2 draws 90 A; channel 0 peaks at 40 A at 6 s. Before enabling, channel 1 draws 200 A."""
    w = LogWriter()
    enabled = w.start("DS:enabled", "boolean", "", BASE)
    for t, on in ((0, False), (1, True), (9, False)):
        w.append(enabled, on, at(t))
    battery = w.start(BATTERY, "double", '{"unit":"V"}', BASE)
    chan = w.start(CHANNELS, "double[]", "", BASE)
    flag = w.start("/SystemStats/BrownedOut", "boolean", "", BASE)
    for n in range(200):
        t = n * 0.05
        sag = 4.0 <= t < 4.2
        w.append(battery, 5.5 if sag else 12.0 - n * 1e-4, at(t))
        w.append(chan, [40.0 if abs(t - 6.0) < 0.03 else 5.0 + n * 1e-3, 200.0 if t < 1 else 3.0, 90.0 if sag else 4.0, 2.0], at(t))
        if brownout:
            w.append(flag, sag, at(t))
    if info:
        w.append(w.start(INFO, "string", "", BASE), json.dumps({"2": "Elevator", "0": "Intake"}), BASE)
    w.save(path)
    return path.name


@pytest.fixture
def power_log(log_dir):
    return build(log_dir / "power.wpilog")


async def test_the_device_that_drew_the_most_when_the_battery_sagged_is_named_from_the_robots_map(power_log):
    await file_tools.wpilog_open(power_log)
    out = text(await data_tools.wpilog_power())
    assert out["scope"] == "enabled" and out["voltage"]["min_v"] == 5.5 and out["voltage"]["brownout_v"] == 6.0 and out["voltage"]["brownout_basis"].startswith("default")
    first = out["top_current"][0]
    assert first["channel"] == 2 and first["name"] == "Elevator" and first["peak_a"] == 90.0 and first["battery_v_then"] == 5.5
    assert out["top_current"][1]["name"] == "Intake" and out["top_current"][1]["peak_a"] == 40.0
    assert out["at_lowest_voltage"][0]["channel"] == 2 and out["at_lowest_voltage"][0]["amps"] == 90.0
    assert all(row.get("peak_a") != 200.0 for row in out["top_current"])  # the 200 A before enabling is outside the scope
    assert out["brownouts"]["count"] == 1 and out["labelled"] is True and "hint" not in out
    assert out["battery"]["chosen_by"] == "convention" and out["currents"] == {"chosen_by": "convention", "topics": [CHANNELS]}


async def test_without_the_robots_map_channels_stay_numbers_and_the_reply_says_how_to_name_them(log_dir):
    await file_tools.wpilog_open(build(log_dir / "plain.wpilog", info=False))
    out = text(await data_tools.wpilog_power())
    assert out["top_current"][0]["channel"] == 2 and "name" not in out["top_current"][0] and "PdhChannels" in out["hint"] and out["labelled"] is False


async def test_a_window_replaces_the_enabled_default(power_log):
    await file_tools.wpilog_open(power_log)
    out = text(await data_tools.wpilog_power(since_ms=0, until_ms=500))
    assert out["scope"] == "window" and out["top_current"][0]["channel"] == 1 and out["top_current"][0]["peak_a"] == 200.0


async def test_current_signals_with_only_a_suggestive_name_are_listed_not_used_until_named(log_dir):
    w = LogWriter()
    w.append(w.start(BATTERY, "double", "", BASE), 12.0, BASE)
    arm = w.start("/Arm/CurrentDraw", "double", "", BASE)
    for n in range(20):
        w.append(arm, 10.0 + n, at(n * 0.1))
    w.save(log_dir / "named.wpilog")
    await file_tools.wpilog_open("named.wpilog")
    out = text(await data_tools.wpilog_power())
    assert out["top_current"] == [] and out["currents"]["topics"] == []
    named = text(await data_tools.wpilog_power(currents=["/Arm/CurrentDraw"]))
    assert named["currents"]["chosen_by"] == "explicit" and named["top_current"][0]["source"] == "/Arm/CurrentDraw" and named["top_current"][0]["peak_a"] == 29.0


async def test_nothing_to_check_says_so_and_a_bad_name_is_an_error(log_dir):
    w = LogWriter()
    w.append(w.start("/Fake/Value", "double", "", BASE), 1.0, BASE)
    w.append(w.start("/Fake/Text", "string", "", BASE), "x", BASE)
    w.save(log_dir / "none.wpilog")
    await file_tools.wpilog_open("none.wpilog")
    out = text(await data_tools.wpilog_power())
    assert out["status"] == "no_match" and "battery=" in out["reason"]
    with pytest.raises(Exception, match="not among the topics"):
        await data_tools.wpilog_power(currents=["/No/Such"])
    with pytest.raises(Exception, match="must be a number"):
        await data_tools.wpilog_power(currents=["/Fake/Text"])

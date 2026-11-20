"""The live checks that record a few seconds: power, mechanism tracking, correlation and pose. Each is the live half of a log tool."""

import asyncio
import json

import ntcore
from envelope_helpers import j
from nt_helpers import wait_for_client_sync


async def test_live_power_names_the_device_that_drew_the_most_when_the_battery_sagged(client, nt_server):
    options = ntcore.PubSubOptions(sendAll=True)
    battery = nt_server.getTopic("/SystemStats/BatteryVoltage").genericPublish("double", options)
    channels = nt_server.getTopic("/RealOutputs/PowerDistribution/ChannelCurrent").genericPublish("double[]", options)
    info = nt_server.getTopic("/RobotInfo/PdhChannels").genericPublish("string")
    info.set(ntcore.Value.makeString(json.dumps({"1": "Elevator"})))
    battery.set(ntcore.Value.makeDouble(12.0))
    channels.set(ntcore.Value.makeDoubleArray([2.0, 3.0]))
    nt_server.flush()
    await wait_for_client_sync(nt_server)

    async def drive():
        for n in range(100):
            sag = 40 <= n < 50
            battery.set(ntcore.Value.makeDouble(5.5 if sag else 12.0 - n * 1e-4))
            channels.set(ntcore.Value.makeDoubleArray([2.0 + n * 1e-4, 90.0 if sag else 3.0 + n * 1e-4]))
            nt_server.flush()
            await asyncio.sleep(0.02)

    task = asyncio.create_task(drive())
    out = j(await client.call_tool("nt4_power", {"duration_s": 2.5}))
    await task
    assert out["voltage"]["min_v"] == 5.5 and out["voltage"]["brownout_basis"].startswith("default")
    first = out["top_current"][0]
    assert first["channel"] == 1 and first["name"] == "Elevator" and first["peak_a"] == 90.0 and first["battery_v_then"] == 5.5
    assert out["at_lowest_voltage"][0]["channel"] == 1


async def test_live_power_with_nothing_to_check_says_so(client, nt_server):
    keep = nt_server.getTopic("/Fake/Value").genericPublish("double")
    keep.set(ntcore.Value.makeDouble(1.0))
    nt_server.flush()
    await wait_for_client_sync(nt_server)
    out = j(await client.call_tool("nt4_power", {"duration_s": 0.2}))
    assert out["status"] == "no_match" and "battery=" in out["reason"]


async def test_live_mechanism_reports_a_step_with_overshoot(client, nt_server):
    options = ntcore.PubSubOptions(sendAll=True)
    goal = nt_server.getTopic("/Elevator/Goal").genericPublish("double", options)
    position = nt_server.getTopic("/Elevator/Position").genericPublish("double", options)
    goal.set(ntcore.Value.makeDouble(0.0))
    position.set(ntcore.Value.makeDouble(0.0))
    nt_server.flush()
    await wait_for_client_sync(nt_server)

    async def drive():
        for n in range(125):
            t = n * 0.02
            d = t - 0.5
            goal.set(ntcore.Value.makeDouble(1.0 if d >= 0 else 0.0))
            m = 0.0 if d < 0 else 1.1 * d / 0.3 if d < 0.3 else 1.1 - 0.1 * (d - 0.3) / 0.3 if d < 0.6 else 1.0
            position.set(ntcore.Value.makeDouble(m + n * 1e-9))
            nt_server.flush()
            await asyncio.sleep(0.02)

    task = asyncio.create_task(drive())
    out = j(await client.call_tool("nt4_mechanism", {"setpoint": "/Elevator/Goal", "measurement": "/Elevator/Position", "duration_s": 2.5}))
    await task
    assert out["steps"]["count"] == 1 and 8.0 <= out["steps"]["overshoot_pct"]["max"] <= 12.5 and out["following_error"]["n"] >= 40


async def test_live_mechanism_with_a_wrong_name_is_an_error(client, nt_server):
    keep = nt_server.getTopic("/Elevator/Goal").genericPublish("double")
    keep.set(ntcore.Value.makeDouble(0.0))
    nt_server.flush()
    await wait_for_client_sync(nt_server)
    result = await client.call_tool("nt4_mechanism", {"setpoint": "/Elevator/Goal", "measurement": "/No/Such", "duration_s": 0.2}, raise_on_error=False)
    assert result.is_error and "not among the topics" in result.content[0].text


async def test_live_correlation_finds_the_lag(client, nt_server):
    import math

    options = ntcore.PubSubOptions(sendAll=True)
    a = nt_server.getTopic("/Motor/Current").genericPublish("double", options)
    b = nt_server.getTopic("/Battery/Sag").genericPublish("double", options)
    a.set(ntcore.Value.makeDouble(0.0))
    b.set(ntcore.Value.makeDouble(0.0))
    nt_server.flush()
    await wait_for_client_sync(nt_server)

    async def drive():
        for n in range(150):
            t = n * 0.02
            a.set(ntcore.Value.makeDouble(math.sin(t * 2.0)))
            b.set(ntcore.Value.makeDouble(math.sin((t - 0.3) * 2.0)))
            nt_server.flush()
            await asyncio.sleep(0.02)

    task = asyncio.create_task(drive())
    out = j(await client.call_tool("nt4_correlate", {"a": "/Motor/Current", "b": "/Battery/Sag", "duration_s": 3.0, "max_lag_s": 0.8}))
    await task
    assert out["best_lag"]["meaning"] == "b follows a" and abs(out["best_lag"]["lag_s"] - 0.3) <= 0.2  # live times are when updates arrived, so a few tens of ms of jitter


async def test_live_pose_reports_a_jump(client, nt_server):
    import struct

    options = ntcore.PubSubOptions(sendAll=True)
    pose = nt_server.getTopic("/DriveState/Pose").genericPublish("struct:Pose2d", options)
    pose.set(ntcore.Value.makeRaw(struct.pack("<ddd", 0.0, 0.0, 0.0)))
    nt_server.flush()
    await wait_for_client_sync(nt_server)

    async def drive():
        for n in range(120):
            x = n * 0.02 + (1.5 if n >= 60 else 0.0)
            pose.set(ntcore.Value.makeRaw(struct.pack("<ddd", x, 0.0, 0.0)))
            nt_server.flush()
            await asyncio.sleep(0.02)

    task = asyncio.create_task(drive())
    out = j(await client.call_tool("nt4_pose", {"duration_s": 2.8}))
    await task
    assert out["pose"] == "/DriveState/Pose" and out["jumps"]["count"] == 1 and abs(out["jumps"]["largest"][0]["meters"] - 1.52) < 0.1

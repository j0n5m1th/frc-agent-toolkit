"""nt4_scan: the same usual-trouble checks as wpilog_scan, run on a few seconds of the live robot."""

import asyncio

import ntcore
from envelope_helpers import j
from nt_helpers import wait_for_client_sync


async def drive(srv, battery, brown, loop, count=100):
    """50 Hz: the battery sags to 5.5 V for a moment with a brownout flag, and the loop takes 35 ms every fifth time."""
    for n in range(count):
        low = 40 <= n < 50
        battery.set(ntcore.Value.makeDouble(5.5 if low else 12.0 - n * 1e-4))
        brown.set(ntcore.Value.makeBoolean(low))
        loop.set(ntcore.Value.makeDouble(35.0 if n % 5 == 0 else 15.0 + n * 1e-4))
        srv.flush()
        await asyncio.sleep(0.02)


def publishers(srv):
    options = ntcore.PubSubOptions(sendAll=True)
    return (srv.getTopic("/SystemStats/BatteryVoltage").genericPublish("double", options),
            srv.getTopic("/SystemStats/BrownedOut").genericPublish("boolean", options),
            srv.getTopic("/RealOutputs/LoggedRobot/FullCycleMS").genericPublish("double", options))


async def test_a_live_scan_finds_the_sag_the_brownout_and_the_slow_loops(client, nt_server):
    battery, brown, loop = publishers(nt_server)
    battery.set(ntcore.Value.makeDouble(12.0))
    brown.set(ntcore.Value.makeBoolean(False))
    loop.set(ntcore.Value.makeDouble(15.0))
    nt_server.flush()
    await wait_for_client_sync(nt_server)
    task = asyncio.create_task(drive(nt_server, battery, brown, loop))
    out = j(await client.call_tool("nt4_scan", {"duration_s": 2.5}))
    await task
    assert out["battery"]["topic"] == "/SystemStats/BatteryVoltage" and out["battery"]["chosen_by"] == "convention" and out["battery"]["min_v"] == 5.5
    assert out["loop"]["topic"].endswith("FullCycleMS") and out["loop"]["max_ms"] == 35.0 and out["brownout_s"]
    assert out["flags"][0].startswith("high: brownout flag") and any(f.startswith("high: battery fell to 5.5 V") for f in out["flags"])
    assert any("a live scan cannot tell when the robot was enabled" in f for f in out["flags"]) and "enabled_periods" not in out


async def test_a_live_scan_of_a_robot_with_no_usual_topics_says_what_it_could_not_check(client, nt_server):
    keep = nt_server.getTopic("/Fake/Value").genericPublish("double")
    keep.set(ntcore.Value.makeDouble(1.0))
    nt_server.flush()
    await wait_for_client_sync(nt_server)
    out = j(await client.call_tool("nt4_scan", {"duration_s": 0.2}))
    assert out["battery"]["checked"] is False and out["loop"]["checked"] is False and out["messages"]["checked"] is False


async def test_a_named_battery_that_is_not_there_is_an_error(client, nt_server):
    keep = nt_server.getTopic("/Fake/Value").genericPublish("double")
    keep.set(ntcore.Value.makeDouble(1.0))
    nt_server.flush()
    await wait_for_client_sync(nt_server)
    result = await client.call_tool("nt4_scan", {"duration_s": 0.2, "battery": "/No/Such"}, raise_on_error=False)
    assert result.is_error and "not among the topics" in result.content[0].text

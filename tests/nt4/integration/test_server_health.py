"""`nt4_health` against a real local NetworkTables server that publishes a Systemcore's health topics."""

import ntcore
import pytest
from nt_helpers import j, publish, wait_for_client_sync

pytestmark = pytest.mark.usefixtures("isolate_capture_logs")


async def test_health_reads_the_controller_topics_and_the_text_of_active_alerts(client, nt_server):
    keep = [
        publish(nt_server, "/sys/battery", "double", ntcore.Value.makeDouble(6.2)),
        publish(nt_server, "/sys/cpu", "double", ntcore.Value.makeDouble(12.0)),
        publish(nt_server, "/sys/brownout", "boolean", ntcore.Value.makeBoolean(False)),
        publish(nt_server, "/Netcomm/Status/HasUserCode", "boolean", ntcore.Value.makeBoolean(True)),
        publish(nt_server, "/Netcomm/Alerts/Arm/0/e1/active", "boolean", ntcore.Value.makeBoolean(True)),
        publish(nt_server, "/Netcomm/Alerts/Arm/0/e1/text", "string", ntcore.Value.makeString("Arm encoder lost")),
        publish(nt_server, "/Netcomm/Alerts/Arm/2/e2/active", "boolean", ntcore.Value.makeBoolean(False)),
        publish(nt_server, "/Netcomm/Alerts/Arm/2/e2/text", "string", ntcore.Value.makeString("never shown")),
    ]
    await wait_for_client_sync(nt_server)
    out = j(await client.call_tool("nt4_health", {}))
    assert out["controller"]["battery_v"] == 6.2 and out["controller"]["robot_code"] == {"running": True}
    assert out["alerts"] == [{"level": "high", "text": "Arm encoder lost"}]
    assert any("battery reads 6.2 V" in f for f in out["flags"])
    assert "never shown" not in str(out)
    del keep


async def test_health_on_a_robot_program_server_says_to_use_port_6810(client, nt_server):
    keep = [publish(nt_server, "/Telemetry/x", "double", ntcore.Value.makeDouble(1.0))]
    await wait_for_client_sync(nt_server)
    out = j(await client.call_tool("nt4_health", {}))
    assert out["controller"]["checked"] is False and "6810" in out["controller"]["why"]
    del keep


async def test_alerts_are_read_first_and_a_cap_on_topics_is_reported(client, nt_server, monkeypatch):
    from frc_mcp_nt4 import health

    monkeypatch.setattr(health, "MAX_TOPICS", 6)
    monkeypatch.setattr("frc_mcp_nt4.tools.health_tools.MAX_TOPICS", 6)
    keep = [publish(nt_server, f"/sys/x{n}", "double", ntcore.Value.makeDouble(1.0)) for n in range(10)]
    keep += [publish(nt_server, "/Netcomm/Alerts/A/0/a1/active", "int", ntcore.Value.makeInteger(1)),
             publish(nt_server, "/Netcomm/Alerts/A/0/a1/text", "string", ntcore.Value.makeString("Late alert"))]
    await wait_for_client_sync(nt_server)
    out = j(await client.call_tool("nt4_health", {}))
    assert out["alerts"] == [{"level": "high", "text": "Late alert"}]  # found although 10 other topics would have filled the cap
    assert out["topics_not_read"] > 0 and any("were not read" in f for f in out["flags"])
    del keep

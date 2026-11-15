"""Publishing, write policy, publisher leases and property tests."""

import asyncio

import ntcore
import pytest
from fastmcp.exceptions import ToolError
from nt_helpers import (
    PORT,
    get_value,
    j,
    publish,
    wait_for_client_sync,
    wait_for_tool,
    wait_until,
)

from frc_mcp_nt4.session import session

pytestmark = pytest.mark.usefixtures("isolate_capture_logs", "allow_test_writes")


async def test_invalid_write_values_are_rejected_before_publication(client, nt_server):
    for topic, type_str, value in [
        ("/bad/bool", "boolean", 2),
        ("/bad/int", "int", 1.5),
        ("/bad/double", "double", True),
        ("/bad/array", "int[]", {"1": 2}),
    ]:
        with pytest.raises(ToolError, match="Cannot convert"):
            await client.call_tool("nt4_set_value", {"topic": topic, "type_str": type_str, "value": value})
        assert not nt_server.getTopic(topic).exists()


async def test_set_new_and_existing(client, nt_server):
    with pytest.raises(ToolError, match="Pass `type`"):
        await client.call_tool("nt4_set_value", {"topic": "/t/x", "value": 1})
    r = j(await client.call_tool("nt4_set_value", {"topic": "/t/x", "value": 2, "type_str": "int"}))
    assert r == {"topic": "/t/x", "type": "int", "sent": 2}
    sub = nt_server.getTopic("/t/x").genericSubscribe()
    assert await wait_until(lambda: sub.get().isValid())  # value crosses the network asynchronously
    assert sub.get().value() == 2
    with pytest.raises(ToolError, match="cannot publish as"):
        await client.call_tool("nt4_set_value", {"topic": "/t/x", "value": 1.5, "type_str": "double"})
    assert j(await client.call_tool("nt4_release_publisher", {"topic": "/t/x"}))["released"] is True


async def test_read_only_blocks_writes(client, monkeypatch):
    monkeypatch.setenv("NT4_READ_ONLY", "1")
    with pytest.raises(ToolError, match="Writes are disabled"):
        await client.call_tool("nt4_set_value", {"topic": "/ro/x", "value": 1, "type_str": "int"})
    with pytest.raises(ToolError, match="Writes are disabled"):
        await client.call_tool("nt4_release_publisher", {"topic": "/ro/x"})
    assert j(await client.call_tool("nt4_status"))["writes_allowed"] is False


async def test_writes_are_disabled_by_default_and_need_an_allowlist(client, monkeypatch):
    monkeypatch.delenv("NT4_ALLOW_WRITES")
    monkeypatch.delenv("NT4_WRITE_PREFIXES")
    status = j(await client.call_tool("nt4_status"))
    assert status["writes_allowed"] is False and "NT4_ALLOW_WRITES" in status["write_reason"]
    # the same refusal sentence the commands server's `deploy` capability produces
    with pytest.raises(ToolError, match=r"Set NT4_ALLOW_WRITES=1 on the MCP server"):
        await client.call_tool("nt4_set_value", {"topic": "/Test/x", "value": 1, "type_str": "int"})

    monkeypatch.setenv("NT4_ALLOW_WRITES", "1")
    with pytest.raises(ToolError, match="NT4_WRITE_PREFIXES"):
        await client.call_tool("nt4_set_value", {"topic": "/Test/x", "value": 1, "type_str": "int"})
    assert j(await client.call_tool("nt4_status"))["writes_allowed"] is False


@pytest.mark.parametrize("name,value", [
    ("NT4_READ_ONLY", "treu"),
    ("NT4_READ_ONLY", ""),
    ("NT4_ALLOW_WRITES", "maybe"),
    ("NT4_WRITE_PREFIXES", "/"),
    ("NT4_WRITE_PREFIXES", "Test"),
    ("NT4_WRITE_PREFIXES", "/Test//unsafe"),
])
async def test_invalid_safety_config_fails_closed(client, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    status = j(await client.call_tool("nt4_status"))
    assert status["writes_allowed"] is False and name in status["write_reason"]
    with pytest.raises(ToolError, match=name):
        await client.call_tool("nt4_set_value", {"topic": "/Test/x", "value": 1, "type_str": "int"})


async def test_write_prefix_allowlist(client, monkeypatch):
    monkeypatch.setenv("NT4_WRITE_PREFIXES", "/Tunable, /Test/")
    with pytest.raises(ToolError, match="outside the writable prefixes"):
        await client.call_tool("nt4_set_value", {"topic": "/Drive/x", "value": 1, "type_str": "int"})
    with pytest.raises(ToolError, match="outside the writable prefixes"):
        await client.call_tool("nt4_set_value", {"topic": "/TunableDanger/x", "value": 1, "type_str": "int"})
    r = j(await client.call_tool("nt4_set_value", {"topic": "/Test/x", "value": 1, "type_str": "int"}))
    assert r["topic"] == "/Test/x"
    r = j(await client.call_tool("nt4_set_value", {"topic": "/Tunable", "value": 1, "type_str": "int"}))
    assert r["topic"] == "/Tunable"


async def test_properties_on_publish_and_retained_survives_release(client, nt_server):
    r = j(await client.call_tool("nt4_set_value", {
        "topic": "/pp/x", "value": 3, "type_str": "int",
        "properties": {"persistent": True, "retained": True, "note": {"unit": "deg"}}}))
    assert r["properties"] == ["note", "persistent", "retained"]
    r = await wait_for_tool(client, "nt4_list_topics", {"prefix": "/pp", "flag": "persistent", "all_props": True},
                            lambda r: r.get("props", {}).get("/pp/x") == {"persistent": True, "retained": True, "note": {"unit": "deg"}})
    assert r["props"]["/pp/x"] == {"persistent": True, "retained": True, "note": {"unit": "deg"}}
    # retained/persistent topics outlive their publisher (spec: Properties)
    await client.call_tool("nt4_release_publisher", {"topic": "/pp/x"})
    # the release leaves a ghost entry (our own record of a retained topic we no longer publish)
    assert await wait_until(lambda: any(name == "/pp/x" for name, _ in session._publishers.ghosts))
    # our own listing keeps it (ntcore itself would drop it for the releasing client; see session._publishers)
    r = j(await client.call_tool("nt4_list_topics", {"prefix": "/pp", "flag": "retained"}))
    assert r["topics"] == {"/pp/x": "int"} and r["props"]["/pp/x"]["retained"] is True
    # and the server really keeps it: a *different* client still sees the topic and its value.
    other = ntcore.NetworkTableInstance.create()
    other.startClient4("observer")
    other.setServer("127.0.0.1", PORT)
    sub = other.getTopic("/pp/x").genericSubscribe()
    assert await wait_until(lambda s=sub: s.get().isValid())
    assert sub.get().value() == 3 and other.getTopic("/pp/x").isRetained()
    del sub
    ntcore.NetworkTableInstance.destroy(other)


async def test_non_retained_topic_disappears_on_release(client, nt_server):
    await client.call_tool("nt4_set_value", {"topic": "/pp/tmp", "value": 1, "type_str": "int"})
    seen = nt_server.getTopic("/pp/tmp").genericSubscribe()
    assert await wait_until(lambda: seen.get().isValid())  # the server has the value before we release it
    await client.call_tool("nt4_release_publisher", {"topic": "/pp/tmp"})
    r = await wait_for_tool(client, "nt4_list_topics", {"prefix": "/pp/tmp"}, lambda r: r["total"] == 0)
    assert r["total"] == 0


async def test_timed_publisher_expires_even_if_writes_are_disabled_afterward(client, nt_server, monkeypatch):
    result = j(await client.call_tool("nt4_set_value", {
        "topic": "/t/lease", "value": 2, "type_str": "int", "publisher_ttl_s": 0.6,
        "read_back": True,
    }))
    assert 0 < result["publisher_expires_in_s"] <= 0.6
    assert result["read_back"]["status"] == "observed"
    assert "not server acknowledgement" in result["read_back"]["note"]
    assert "/t/lease" in j(await client.call_tool("nt4_status"))["publisher_expiry_s"]
    monkeypatch.setenv("NT4_READ_ONLY", "1")
    assert await wait_until(lambda: "/t/lease" not in session._publishers, timeout=2)
    assert await wait_until(lambda: not nt_server.getTopic("/t/lease").exists(), timeout=3)


async def test_timed_publisher_does_not_extend_unless_explicitly_requested(client):
    await client.call_tool("nt4_set_value", {"topic": "/t/timed", "value": 1, "type_str": "int", "publisher_ttl_s": 3})
    deadline = session._publishers.deadline("/t/timed")
    await client.call_tool("nt4_set_value", {"topic": "/t/timed", "value": 2})
    assert session._publishers.deadline("/t/timed") == deadline
    await client.call_tool("nt4_set_value", {"topic": "/t/timed", "value": 3, "publisher_ttl_s": 5})
    assert session._publishers.deadline("/t/timed") > deadline
    await client.call_tool("nt4_release_publisher", {"topic": "/t/timed"})
    assert not session._publishers.has_timer("/t/timed")


async def test_old_lease_cannot_release_a_republished_topic(client):
    await client.call_tool("nt4_set_value", {"topic": "/t/repub", "value": 1, "type_str": "int", "publisher_ttl_s": 0.4})
    await client.call_tool("nt4_release_publisher", {"topic": "/t/repub"})
    await client.call_tool("nt4_set_value", {"topic": "/t/repub", "value": 2, "type_str": "int"})
    # deliberate quiet window: the old 0.4 s lease must expire and must not release the republished topic
    await asyncio.sleep(0.7)
    assert "/t/repub" in session._publishers


async def test_retained_value_can_outlive_timed_publisher(client, nt_server):
    observer = ntcore.NetworkTableInstance.create()
    observer.startClient4("lease-observer")
    observer.setServer("127.0.0.1", PORT)
    sub = observer.getTopic("/t/retained").genericSubscribe()
    try:
        await client.call_tool("nt4_set_value", {
            "topic": "/t/retained", "value": 3, "type_str": "int",
            "properties": {"retained": True}, "publisher_ttl_s": 2,
        })
        assert await wait_until(lambda: sub.get().isValid() and observer.getTopic("/t/retained").isRetained(), timeout=1.5)
        assert await wait_until(lambda: "/t/retained" not in session._publishers, timeout=3)
        assert sub.get().value() == 3 and observer.getTopic("/t/retained").isRetained()
    finally:
        sub = None
        ntcore.NetworkTableInstance.destroy(observer)


async def test_publisher_expiry_options_reject_conflicting_operations(client, nt_server):
    keep = publish(nt_server, "/t/props", "double", ntcore.Value.makeDouble(1))
    await wait_for_client_sync(nt_server)
    with pytest.raises(ToolError, match="properties-only"):
        await client.call_tool("nt4_set_value", {
            "topic": "/t/props", "properties": {"retained": True}, "publisher_ttl_s": 1,
        })
    assert not nt_server.getTopic("/t/props").isRetained()
    for ttl in (True, 0, -1, 3601):
        with pytest.raises(ToolError):
            await client.call_tool("nt4_set_value", {"topic": "/t/props", "value": 2, "publisher_ttl_s": ttl})
    del keep


async def test_readback_of_weak_default_does_not_claim_acceptance(client, nt_server):
    keep = publish(nt_server, "/d/readback", "int", ntcore.Value.makeInteger(5))
    await wait_for_client_sync(nt_server)
    result = j(await client.call_tool("nt4_set_value", {
        "topic": "/d/readback", "value": 9, "default": True, "read_back": True,
    }))
    assert result["sent"] == 9
    assert result["read_back"]["status"] == "observed" and result["read_back"]["v"] == 5
    assert "not server acknowledgement" in result["read_back"]["note"]
    del keep


async def test_properties_only_update_and_delete(client, nt_server):
    keep = publish(nt_server, "/pq/x", "double", ntcore.Value.makeDouble(1))
    await wait_for_client_sync(nt_server)
    r = j(await client.call_tool("nt4_set_value", {"topic": "/pq/x", "properties": {"persistent": True, "tag": "a"}}))
    assert r == {"topic": "/pq/x", "properties": ["persistent", "tag"]}
    r = await wait_for_tool(client, "nt4_list_topics", {"prefix": "/pq", "all_props": True},
                            lambda r: r.get("props", {}).get("/pq/x") == {"persistent": True, "tag": "a"})
    assert r["props"]["/pq/x"] == {"persistent": True, "tag": "a"}
    await client.call_tool("nt4_set_value", {"topic": "/pq/x", "properties": {"tag": None, "persistent": False}})
    r = await wait_for_tool(client, "nt4_list_topics", {"prefix": "/pq", "all_props": True},
                            lambda r: "tag" not in r.get("props", {}).get("/pq/x", {}))
    assert "props" not in r or "tag" not in r["props"].get("/pq/x", {})
    with pytest.raises(ToolError, match="does not exist"):
        await client.call_tool("nt4_set_value", {"topic": "/pq/none", "properties": {"retained": True}})
    with pytest.raises(ToolError, match="must be true or false"):
        await client.call_tool("nt4_set_value", {"topic": "/pq/x", "properties": {"persistent": "yes"}})
    with pytest.raises(ToolError, match="Nothing to do"):
        await client.call_tool("nt4_set_value", {"topic": "/pq/x"})
    del keep


async def test_set_default_is_weak(client, nt_server):
    # fresh topic: the default becomes the value
    r = j(await client.call_tool("nt4_set_value", {"topic": "/d/fresh", "value": 3, "type_str": "int", "default": True}))
    assert r["default"] is True and r["default_applied"] is True
    await wait_for_tool(client, "nt4_get_values", {"topics": ["/d/fresh"]}, lambda r: r["/d/fresh"].get("v") == 3)
    assert (await get_value(client, "/d/fresh"))["v"] == 3
    # a value someone else set strongly is not overridden by our default
    keep = publish(nt_server, "/d/strong", "int", ntcore.Value.makeInteger(5))
    await wait_for_client_sync(nt_server)
    refused = j(await client.call_tool("nt4_set_value", {"topic": "/d/strong", "value": 9, "default": True}))
    assert refused["default_applied"] is False  # the robot's real value was kept
    await asyncio.sleep(0.4)  # deliberate quiet window: the refused default must not land late either
    assert (await get_value(client, "/d/strong"))["v"] == 5
    # but a normal set does override
    await client.call_tool("nt4_set_value", {"topic": "/d/strong", "value": 9})
    await wait_for_tool(client, "nt4_get_values", {"topics": ["/d/strong"]}, lambda r: r["/d/strong"].get("v") == 9)
    assert (await get_value(client, "/d/strong"))["v"] == 9
    del keep


async def test_read_only_blocks_property_changes(client, nt_server, monkeypatch):
    keep = publish(nt_server, "/ro2/x", "double", ntcore.Value.makeDouble(1))
    await wait_for_client_sync(nt_server)
    monkeypatch.setenv("NT4_READ_ONLY", "1")
    with pytest.raises(ToolError, match="Writes are disabled"):
        await client.call_tool("nt4_set_value", {"topic": "/ro2/x", "properties": {"retained": True}})
    del keep


async def test_warns_on_immutable_topic(client, nt_server):
    keep = publish(nt_server, "/im/x", "string", ntcore.Value.makeString("a"))
    nt_server.getTopic("/im/x").setProperty("mutable", False)
    await wait_for_tool(client, "nt4_list_topics", {"prefix": "/im", "all_props": True},
                        lambda r: r.get("props", {}).get("/im/x", {}).get("mutable") is False)
    r = j(await client.call_tool("nt4_set_value", {"topic": "/im/x", "value": "b"}))
    assert "mutable=false" in r["warning"]
    r = j(await client.call_tool("nt4_set_value", {"topic": "/im/x", "properties": {"k": 1}}))
    assert "warning" in r
    del keep


async def test_released_retained_topic_reappears_when_republished_and_clears_on_disconnect(client, nt_server):
    await client.call_tool("nt4_set_value", {"topic": "/gh/x", "value": 1, "type_str": "int", "properties": {"retained": True}})
    seen = nt_server.getTopic("/gh/x").genericSubscribe()
    assert await wait_until(lambda: seen.get().isValid())  # the server has the retained value before we release it
    await client.call_tool("nt4_release_publisher", {"topic": "/gh/x"})
    assert await wait_until(lambda: any(name == "/gh/x" for name, _ in session._publishers.ghosts))
    assert "/gh/x" in j(await client.call_tool("nt4_list_topics", {"prefix": "/gh"}))["topics"]
    await client.call_tool("nt4_set_value", {"topic": "/gh/x", "value": 2})  # live again: no duplicate
    r = await wait_for_tool(client, "nt4_list_topics", {"prefix": "/gh"}, lambda r: list(r.get("topics", {})) == ["/gh/x"])
    assert list(r["topics"]) == ["/gh/x"]
    await client.call_tool("nt4_release_publisher", {"topic": "/gh/x"})
    await client.call_tool("nt4_connection", {"action": "disconnect"})
    assert session._publishers.ghosts == []


async def test_uncached_flag(client, nt_server):
    keep = [publish(nt_server, "/uc/a", "double", ntcore.Value.makeDouble(1)),
            publish(nt_server, "/uc/b", "double", ntcore.Value.makeDouble(2))]
    nt_server.getTopic("/uc/a").setCached(False)
    r = await wait_for_tool(client, "nt4_list_topics", {"prefix": "/uc", "flag": "uncached"},
                            lambda r: list(r.get("topics", {})) == ["/uc/a"])
    assert list(r["topics"]) == ["/uc/a"] and r["props"]["/uc/a"] == {"cached": False}
    del keep


async def test_set_value_echoes_value(client, nt_server):
    r = j(await client.call_tool("nt4_set_value", {"topic": "/ec/x", "value": 1.23456789, "type_str": "double"}))
    assert r["sent"] == 1.2346 and r["type"] == "double"
    r = j(await client.call_tool("nt4_set_value", {"topic": "/ec/arr", "value": [1, 2], "type_str": "int[]"}))
    assert r["sent"] == [1, 2]


@pytest.mark.parametrize("args, message", [
    ({"value": "maybe", "type_str": "boolean"}, "not a boolean"),
    ({"value": "1", "type_str": "bogus"}, "unknown NT4 type"),
    ({"value": 2**70, "type_str": "int"}, "64-bit"),
    ({"value": "zz", "type_str": "raw"}, "Cannot convert"),
    ({"value": "[1,2", "type_str": "double[]"}, "Cannot convert"),
])
async def test_bad_values_give_clear_errors(client, args, message):
    with pytest.raises(ToolError, match=message) as e:
        await client.call_tool("nt4_set_value", {"topic": "/bad/x", **args})
    assert "\n" not in str(e.value).strip() or len(str(e.value)) < 400  # no multi-line pybind dumps


async def test_dict_to_string_and_known_raw_types_accepted(client, nt_server):
    r = j(await client.call_tool("nt4_set_value", {"topic": "/ok/s", "value": {"a": [1]}, "type_str": "string"}))
    assert r["sent"] == '{"a": [1]}'  # JSON, not Python repr
    r = j(await client.call_tool("nt4_set_value", {"topic": "/ok/st", "value": "00", "type_str": "struct:Whatever"}))
    sent = r["sent"]  # an unannounced schema: hex plus the reason it stayed hex
    assert sent["hex"] == "00" and "not announced" in sent["decode_error"]


async def test_writing_a_chooser_echo_topic_points_at_the_input_topic(client, nt_server, monkeypatch):
    monkeypatch.setenv("NT4_WRITE_PREFIXES", "/ch")
    keep = [publish(nt_server, "/ch/selected/value", "string", ntcore.Value.makeString("A")),
            publish(nt_server, "/ch/selected/tune", "string", ntcore.Value.makeString("A")),
            publish(nt_server, "/plain/selected/value", "string", ntcore.Value.makeString("A"))]
    await wait_for_client_sync(nt_server)
    echoed = j(await client.call_tool("nt4_set_value", {"topic": "/ch/selected/value", "value": "B"}))
    assert "selected/tune" in echoed["chooser_note"]
    monkeypatch.setenv("NT4_WRITE_PREFIXES", "/plain")
    assert "chooser_note" not in j(await client.call_tool("nt4_set_value", {"topic": "/plain/selected/value", "value": "B"}))
    del keep


async def test_set_value_can_stamp_a_sample_with_a_server_time(client, nt_server):
    # the clock offset arrives a moment after connecting
    status = await wait_for_tool(client, "nt4_status", {}, lambda s: "server_time_us" in s)
    assert "server_time_us" in status and "clock_offset_us" in status
    stamp = status["server_time_us"] - 5_000_000  # five seconds ago
    sent = j(await client.call_tool("nt4_set_value", {"topic": "/t/ts/v", "value": 1.5, "type_str": "double", "timestamp_us": stamp}))
    assert sent["timestamp_us"] == stamp
    watcher = nt_server.getTopic("/t/ts/v").genericSubscribe()
    assert await wait_until(lambda: watcher.get().isValid())
    stored = watcher.get()
    assert stored.value() == 1.5 and abs(stored.time() - stamp) < 50_000  # server clock, within sync error
    plain = j(await client.call_tool("nt4_set_value", {"topic": "/t/ts/v", "value": 2.5}))
    assert "timestamp_us" not in plain
    own = j(await client.call_tool("nt4_get_values", {"topics": ["/t/ts/v"], "timestamps": True}))
    assert own["/t/ts/v"]["ts_us"] is None  # a value we published ourselves: the server's stamp is unknown locally


@pytest.mark.parametrize(("args", "message"), [
    ({"timestamp_us": 0}, "positive whole number"),
    ({"timestamp_us": -5}, "positive whole number"),
    ({"timestamp_us": 10**18}, "in the future"),
    ({"timestamp_us": 5, "default": True}, "cannot be combined with default"),
    ({"timestamp_us": 5, "value": None, "properties": {"retained": True}}, "needs a value"),
])
async def test_bad_timestamps_are_rejected(client, nt_server, args, message):
    await wait_for_tool(client, "nt4_status", {}, lambda s: "server_time_us" in s)  # the clock offset arrives after connecting
    call = {"topic": "/t/ts/bad", "value": 1.0, "type_str": "double", **args}
    if call["value"] is None:
        del call["value"]
    with pytest.raises(ToolError, match=message):
        await client.call_tool("nt4_set_value", call)

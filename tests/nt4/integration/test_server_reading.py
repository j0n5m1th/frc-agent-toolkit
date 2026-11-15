"""Topic listing, value reading, widget groups, waiting and inspection tests."""

import asyncio
import struct

import ntcore
import pytest
from fastmcp.exceptions import ToolError
from nt_helpers import (
    PORT,
    j,
    make_chooser,
    publish,
    publish_schemas,
    read_and_discard,
    wait_for_capture_samples,
    wait_for_client_sync,
    wait_for_tool,
    wait_until,
)

from frc_mcp_nt4.session import session

pytestmark = pytest.mark.usefixtures("isolate_capture_logs", "allow_test_writes")


async def test_list_topics_flat_tree_and_props(client, nt_server):
    keep = [
        publish(nt_server, "/a/x", "double", ntcore.Value.makeDouble(1)),
        publish(nt_server, "/a/y/z", "boolean", ntcore.Value.makeBoolean(True)),
        publish(nt_server, "/b", "string", ntcore.Value.makeString("s")),
        publish(nt_server, "/a/.type", "string", ntcore.Value.makeString("meta")),
    ]
    nt_server.getTopic("/b").setProperties({"source": "app"})
    nt_server.getTopic("/b").setPersistent(True)
    await wait_for_client_sync(nt_server)
    r = j(await client.call_tool("nt4_list_topics", {}))
    assert r["topics"] == {"/a/x": "double", "/a/y/z": "boolean", "/b": "string"}
    assert r["props"] == {"/b": {"persistent": True}}  # empty/app-specific props are omitted
    assert r["hidden_matches"] == 1 and r["hidden_examples"] == ["/a/.type"]
    r = j(await client.call_tool("nt4_list_topics", {"name_contains": "nomatch"}))
    assert "hidden_matches" not in r  # counts only matches that were omitted
    r = j(await client.call_tool("nt4_list_topics", {"all_props": True, "include_hidden": True}))
    assert r["props"]["/b"] == {"persistent": True, "source": "app"} and "/a/.type" in r["topics"]
    r = j(await client.call_tool("nt4_list_topics", {"depth": 1}))
    assert r["groups"] == {"/a/": 2} and r["topics"] == {"/b": "string"}
    r = j(await client.call_tool("nt4_list_topics", {"flag": "persistent"}))
    assert list(r["topics"]) == ["/b"]
    del keep


async def test_topic_pagination_combines_groups_and_leaves_within_budget(client, nt_server, monkeypatch):
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "500")
    keep = []
    expected_groups = set()
    for i in range(12):
        prefix = f"/paging/group{i:02d}-" + "x" * 45
        expected_groups.add(prefix + "/")
        for suffix in ("a", "b"):
            keep.append(publish(nt_server, prefix + "/" + suffix, "double", ntcore.Value.makeDouble(i)))
    keep.append(publish(nt_server, "/paging/leaf", "double", ntcore.Value.makeDouble(1)))
    await wait_for_client_sync(nt_server, timeout=0.5)  # the client only reads values for the first ~24 topics until asked, so waiting for all of them would time out (5 s)
    offset, groups_seen, topics_seen = 0, {}, {}
    while True:
        result = await client.call_tool("nt4_list_topics", {
            "prefix": "/paging/", "depth": 1, "limit": 4, "offset": offset,
        })
        assert len(result.content[0].text) <= 500
        page = j(result)
        assert page["total"] == 25 and page["total_items"] == 13
        assert not (groups_seen.keys() & page.get("groups", {}).keys())
        groups_seen.update(page.get("groups", {}))
        topics_seen.update(page["topics"])
        if "next_offset" not in page:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert set(groups_seen) == expected_groups and all(count == 2 for count in groups_seen.values())
    assert topics_seen == {"/paging/leaf": "double"}
    del keep


async def test_topic_pagination_never_skips_an_oversized_item(client, monkeypatch):
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "500")
    monkeypatch.setattr(session, "list_topics", lambda *_, **__: ([{"name": "/" + "x" * 2000, "type": "double", "props": {}}], []))
    result = await client.call_tool("nt4_list_topics", {})
    page = j(result)
    assert len(result.content[0].text) <= 500
    assert page["truncated"] and page["blocked_offset"] == 0
    assert "next_offset" not in page


async def test_get_values_and_missing(client, nt_server):
    keep = publish(nt_server, "/robot/speed", "double", ntcore.Value.makeDouble(1.23456789))
    r = j(await client.call_tool("nt4_get_values", {"topics": ["/robot/speed", "/nope"], "wait_s": 1}))
    assert r["/robot/speed"] == {"v": 1.2346}
    assert r["/nope"] == {"err": "not announced"}
    assert session.capture_registry._captures == {} and "ts_us" not in r["/robot/speed"]
    del keep


async def test_value_and_capture_freshness_is_optional_and_explained(client, nt_server):
    keep = publish(nt_server, "/age/value", "double", ntcore.Value.makeDouble(42))
    await wait_for_client_sync(nt_server)
    values = j(await client.call_tool("nt4_get_values", {"topics": ["/age/value"], "include_age": True}))
    age = values["/age/value"]["last_update"]
    assert (age["age_ms"] is not None and age["age_ms"] >= 0) or age["reason"]
    group = j(await client.call_tool("nt4_get_widgets", {"bases": ["/age"], "include_age": True}))
    assert "value" in group["/age"]["last_updates"]
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/age/value"]}))
    await wait_for_capture_samples(started["id"], "/age/value")
    result = j(await read_and_discard(client, {
        "capture_id": started["id"], "include_evidence": True,
    }))
    evidence = result["evidence"]["/age/value"]
    assert evidence["retained_samples"] >= 1 and len(evidence["retained_ms"]) == 2
    assert evidence["dropped"] == 0
    assert "age_ms" in evidence["last_update"]
    del keep


async def test_struct_decoded_from_server_schema(client, nt_server):
    keep = publish_schemas(nt_server)
    keep.append(publish(nt_server, "/pose", "struct:Pose2d",
                        ntcore.Value.makeRaw(struct.pack("<ddd", 1.0, 2.0, 0.5))))
    word = (2 << 56) | (1 << 58) | (1 << 61)  # teleoperated, enabled, dsAttached
    keep.append(publish(nt_server, "/cw", "struct:ControlWord", ntcore.Value.makeRaw(word.to_bytes(8, "little"))))
    keep.append(publish(nt_server, "/poses", "struct:Pose2d[]",
                        ntcore.Value.makeRaw(struct.pack("<dddddd", 1, 2, 3, 4, 5, 6))))
    await wait_for_client_sync(nt_server)
    r = j(await client.call_tool("nt4_get_values", {"topics": ["/pose", "/cw", "/poses"]}))
    assert r["/pose"]["v"] == {"translation.x": 1.0, "translation.y": 2.0, "rotation.value": 0.5}
    assert r["/cw"]["v"] == {"opModeHash": 0, "robotMode": "teleoperated", "enabled": True,
                             "eStop": False, "fmsAttached": False, "dsAttached": True}
    assert len(r["/poses"]["v"]) == 2 and r["/poses"]["v"][1]["rotation.value"] == 6.0
    r = j(await client.call_tool("nt4_get_values", {"topics": ["/pose"], "fields": ["x"]}))
    assert r["/pose"]["v"] == {"translation.x": 1.0}


async def test_unknown_struct_falls_back_to_hex(client, nt_server):
    keep = publish(nt_server, "/mystery", "struct:Nope", ntcore.Value.makeRaw(b"\x01\x02"))
    r = j(await client.call_tool("nt4_get_values", {"topics": ["/mystery"]}))
    v = r["/mystery"]["v"]
    assert v["hex"] == "0102" and "not announced" in v["decode_error"]  # hex plus the reason it stayed hex
    del keep


async def test_large_values_truncated(client, nt_server):
    keep = publish(nt_server, "/big", "double[]", ntcore.Value.makeDoubleArray([0.5] * 1000))
    r = j(await client.call_tool("nt4_get_values", {"topics": ["/big"]}))
    v = r["/big"]["v"]
    assert v["truncated"] and v["len"] == 1000 and len(v["head"]) == 50
    del keep


async def test_response_cap(client, nt_server, monkeypatch):
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "1000")
    keep = [publish(nt_server, f"/many/{i}", "string", ntcore.Value.makeString("x" * 300)) for i in range(60)]
    await wait_for_client_sync(nt_server, timeout=0.5)  # the client only reads values for the first ~24 topics until asked, so waiting for all of them would time out (5 s)
    topics = [f"/many/{i}" for i in range(50)]
    result = await client.call_tool("nt4_get_values", {"topics": topics})
    r = j(result)
    assert len(result.content[0].text) <= 1000
    assert r["truncated"] and r["omitted_items"] > 0
    assert r["partial"] and all("type" not in item for item in r["partial"].values())
    del keep


async def test_wait_for_nan_requires_a_valid_comparison_value(client, nt_server):
    pub = nt_server.getTopic("/w/nan").genericPublish("double")
    pub.set(ntcore.Value.makeDouble(float("nan")))
    await wait_for_client_sync(nt_server)
    timed_out = j(await client.call_tool("nt4_wait_for", {
        "topic": "/w/nan", "op": "!=", "value": 40, "wait_s": 0.5,
    }))
    assert timed_out["met"] is False and timed_out["v"] == "nan"

    async def publish_valid():
        assert await wait_until(lambda: session.active_operations > 0)  # the wait call is in flight
        pub.set(ntcore.Value.makeDouble(50))

    task = asyncio.create_task(publish_valid())
    matched = j(await client.call_tool("nt4_wait_for", {
        "topic": "/w/nan", "op": ">", "value": 40, "wait_s": 3,
    }))
    await task
    assert matched["met"] is True and matched["v"] == 50


async def test_wait_for_conditions(client, nt_server):
    keep = publish_schemas(nt_server)
    pub = nt_server.getTopic("/w/n").genericPublish("double")
    pub.set(ntcore.Value.makeDouble(1.0))
    await wait_for_client_sync(nt_server)
    # already true -> immediate
    r = j(await client.call_tool("nt4_wait_for", {"topic": "/w/n", "op": "<", "value": 5, "wait_s": 2}))
    assert r["met"] is True and r["v"] == 1.0 and r["waited_s"] < 2  # returned on the first reading, not at wait_s
    # becomes true later
    async def bump():
        # deterministic: the value changes only once the wait call is in flight, so its first reading (1.0) misses
        assert await wait_until(lambda: session.active_operations > 0)
        pub.set(ntcore.Value.makeDouble(7.0))
    task = asyncio.create_task(bump())
    r = j(await client.call_tool("nt4_wait_for", {"topic": "/w/n", "op": ">", "value": 5, "wait_s": 3}))
    await task
    assert r["met"] is True and r["v"] == 7.0 and r["waited_s"] < 3  # met before the timeout ceiling
    # a brief pulse between polls is not missed (sendAll queue)
    async def pulse():
        # deliberate: the pulse must land while the wait is already polling, after its first reading
        await asyncio.sleep(0.3)
        for v in (20.0, 0.0):
            pub.set(ntcore.Value.makeDouble(v))
    task = asyncio.create_task(pulse())
    r = j(await client.call_tool("nt4_wait_for", {"topic": "/w/n", "op": ">=", "value": 20, "wait_s": 3}))
    await task
    assert r["met"] is True
    # timeout is a normal result, not an error
    r = j(await client.call_tool("nt4_wait_for", {"topic": "/w/n", "op": "==", "value": 99, "wait_s": 0.5}))
    assert r["met"] is False and r["v"] == 0.0
    # topic that never appears
    r = j(await client.call_tool("nt4_wait_for", {"topic": "/w/none", "op": "exists", "wait_s": 0.4}))
    assert r["met"] is False and "note" in r
    # struct field + bool
    word = (1 << 58).to_bytes(8, "little")
    cw = nt_server.getTopic("/w/cw").genericPublish("struct:ControlWord")
    cw.set(ntcore.Value.makeRaw(bytes(8)))
    await wait_for_client_sync(nt_server)
    async def enable():
        assert await wait_until(lambda: session.active_operations > 0)  # the wait call is in flight
        cw.set(ntcore.Value.makeRaw(word))
    task = asyncio.create_task(enable())
    r = j(await client.call_tool("nt4_wait_for", {"topic": "/w/cw", "op": "==", "value": True, "field": "enabled", "wait_s": 3}))
    await task
    assert r["met"] is True and r["v"] == {"enabled": True}
    with pytest.raises(ToolError, match="needs a `value`"):
        await client.call_tool("nt4_wait_for", {"topic": "/w/n", "op": ">", "wait_s": 0.5})
    # changed
    async def change():
        # deliberate: the 'changed' baseline is the value at the wait's first reading, so the change must come after it
        await asyncio.sleep(0.3)
        pub.set(ntcore.Value.makeDouble(123.0))
    task = asyncio.create_task(change())
    r = j(await client.call_tool("nt4_wait_for", {"topic": "/w/n", "op": "changed", "wait_s": 3}))
    await task
    assert r["met"] is True and r["v"] == 123.0
    del keep


async def test_inspect_clients_publishers_subscribers(client, nt_server):
    def one(reply: dict, key: str) -> dict:
        """Every `nt4_inspect` answer is one `targets` map; unwrap the entry this call asked for."""
        return reply["targets"][key]

    me = j(await client.call_tool("nt4_inspect", {"what": "clients"}))
    assert one(me, "")["n"] >= 1 and any(r["id"] == "(self)" for r in one(me, "")["rows"])
    # our own publisher shows up under our client name; a server-side publisher shows as ''
    await client.call_tool("nt4_set_value", {"topic": "/i/mine", "value": 1, "type_str": "int"})
    keep = publish(nt_server, "/i/server", "double", ntcore.Value.makeDouble(1))
    await wait_for_client_sync(nt_server)
    pubs = j(await client.call_tool("nt4_inspect", {"what": "publishers", "target": "/i/mine"}))
    assert [r["client"] for r in one(pubs, "/i/mine")["rows"]] == ["(self)"]
    pubs = j(await client.call_tool("nt4_inspect", {"what": "publishers", "target": "/i/server"}))
    assert [r["client"] for r in one(pubs, "/i/server")["rows"]] == ["(server)"]
    # a subscriber with options is visible
    other = ntcore.NetworkTableInstance.create()
    other.startClient4("watcher")
    other.setServer("127.0.0.1", PORT)
    sub = other.getTopic("/i/server").genericSubscribe(ntcore.PubSubOptions(periodic=0.5))
    # the watcher's subscription reaches the server asynchronously
    subs = await wait_for_tool(client, "nt4_inspect", {"what": "subscribers", "target": "/i/server"},
                               lambda r: any(x["client"].startswith("watcher") for x in one(r, "/i/server")["rows"]))
    row = next((r for r in one(subs, "/i/server")["rows"] if r["client"].startswith("watcher")), None)
    assert row is not None
    assert row["options"] == {"periodic": 0.5}
    cs = j(await client.call_tool("nt4_inspect", {"what": "client_subs", "target": row["client"]}))
    assert one(cs, row["client"])["rows"][0]["topics"] == ["/i/server"]
    with pytest.raises(ToolError, match="`target`"):
        await client.call_tool("nt4_inspect", {"what": "publishers"})
    del sub, keep
    ntcore.NetworkTableInstance.destroy(other)


async def test_group_chooser_collapses_to_one_unit(client, nt_server):
    keep = make_chooser(nt_server, "/Auto", "String Chooser", ["A", "B", "C"], "B", "", "selected", "")
    keep += make_chooser(nt_server, "/Dog", "Selectable", ["X", "Y"], "", "", "selected/value", "Y")
    keep += make_chooser(nt_server, "/Active", "String Chooser", ["P", "Q"], "P", "Q", "selected", "")
    await wait_for_client_sync(nt_server)
    r = j(await client.call_tool("nt4_get_widgets", {"bases": ["/Auto", "/Dog", "/Active"]}))
    assert r["/Auto"] == {"type": "String Chooser", "options": ["A", "B", "C"], "default": "B",
                          "active": "", "selected": "", "current": "B"}  # nothing selected -> default
    assert r["/Dog"]["selected"] == "Y" and r["/Dog"]["current"] == "Y"  # nested selected/value layout
    assert r["/Active"]["current"] == "Q"  # active beats default
    del keep


async def test_invalid_chooser_selection_is_not_reported_as_robot_choice(client, nt_server):
    keep = make_chooser(nt_server, "/Invalid", "String Chooser", ["A", "B"], "A", "A", "selected", "bad")
    await wait_for_client_sync(nt_server)
    result = j(await client.call_tool("nt4_get_widgets", {"bases": ["/Invalid"]}))["/Invalid"]
    assert result["selected"] == "bad" and result["current"] == "bad"  # the published value, with the warning naming it
    assert "selected" in result["warning"]
    del keep


async def test_group_chooser_reports_announced_selection_without_a_value(client, nt_server):
    keep = [
        publish(nt_server, "/Pending/.type", "string", ntcore.Value.makeString("String Chooser")),
        publish(nt_server, "/Pending/options", "string[]", ntcore.Value.makeStringArray(["A", "B"])),
        publish(nt_server, "/Pending/default", "string", ntcore.Value.makeString("A")),
        publish(nt_server, "/Pending/active", "string", ntcore.Value.makeString("")),
        nt_server.getTopic("/Pending/selected").genericPublish("string"),
    ]
    await wait_for_client_sync(nt_server)
    result = j(await client.call_tool("nt4_get_widgets", {
        "bases": ["/Pending"], "wait_s": 0.4,
    }))["/Pending"]
    assert "current" not in result and "unavailable" in result["warning"]
    assert result["unavailable"]["selected"] == {"err": "no value yet"}
    keep[-1].set(ntcore.Value.makeString("B"))
    await wait_for_client_sync(nt_server)
    result = j(await client.call_tool("nt4_get_widgets", {"bases": ["/Pending"]}))["/Pending"]
    assert result["current"] == "B" and "unavailable" not in result
    del keep


async def test_group_generic_widget_and_missing(client, nt_server):
    keep = [
        publish(nt_server, "/F/.type", "string", ntcore.Value.makeString("Field2d")),
        publish(nt_server, "/F/robot", "double[]", ntcore.Value.makeDoubleArray([1.0, 2.0, 0.0])),
        publish(nt_server, "/F/obj/.type", "string", ntcore.Value.makeString("line")),
        publish(nt_server, "/F/obj/len", "double", ntcore.Value.makeDouble(0.5)),
    ]
    await wait_for_client_sync(nt_server)
    r = j(await client.call_tool("nt4_get_widgets", {"bases": ["/F", "/nothing"]}))
    assert r["/F"] == {"type": "Field2d", "v": {"robot": [1.0, 2.0, 0.0], "obj/len": 0.5}}  # nested .type dropped
    assert r["/nothing"] == {"err": "no topics under this path"}
    del keep


async def test_hidden_examples_only_when_few(client, nt_server):
    keep = [publish(nt_server, f"/hx/n{i}/.type", "string", ntcore.Value.makeString("t")) for i in range(7)]
    keep += [publish(nt_server, "/hx/visible", "double", ntcore.Value.makeDouble(1))]
    await wait_for_client_sync(nt_server)
    r = j(await client.call_tool("nt4_list_topics", {"prefix": "/hx"}))
    assert r["hidden_matches"] == 7 and "hidden_examples" not in r  # too many to name: just the count
    r = j(await client.call_tool("nt4_list_topics", {"prefix": "/hx", "name_contains": "n1"}))
    assert r["hidden_matches"] == 1 and r["hidden_examples"] == ["/hx/n1/.type"]
    del keep


async def test_depth_listing_shows_single_topic_groups_directly(client, nt_server):
    keep = [publish(nt_server, "/dg/a/x", "double", ntcore.Value.makeDouble(1)),
            publish(nt_server, "/dg/b/y", "double", ntcore.Value.makeDouble(1)),
            publish(nt_server, "/dg/b/z", "double", ntcore.Value.makeDouble(1))]
    await wait_for_client_sync(nt_server)
    r = j(await client.call_tool("nt4_list_topics", {"prefix": "/dg", "depth": 1}))
    assert r["topics"] == {"/dg/a/x": "double"}  # a group of one is listed as the topic itself
    assert r["groups"] == {"/dg/b/": 2}
    del keep


async def test_inspect_unknown_target_message(client, nt_server):
    out = j(await client.call_tool("nt4_inspect", {"what": "publishers", "target": "/does/not/exist"}))
    assert "no such meta-topic" in out["targets"]["/does/not/exist"]["err"]


async def test_declared_units_are_surfaced_in_reads_listings_and_captures(client, nt_server):
    topic = nt_server.getTopic("/un/angle")
    keep = [topic.genericPublishEx("double", {"unit": "deg"}),
            publish(nt_server, "/un/plain", "double", ntcore.Value.makeDouble(1.0))]
    keep[0].set(ntcore.Value.makeDouble(12.5))
    await wait_for_client_sync(nt_server)
    values = j(await client.call_tool("nt4_get_values", {"topics": ["/un/angle", "/un/plain"]}))
    assert values["/un/angle"]["unit"] == "deg" and "unit" not in values["/un/plain"]
    listing = j(await client.call_tool("nt4_list_topics", {"prefix": "/un/"}))
    assert listing["props"] == {"/un/angle": {"unit": "deg"}}  # shown without all_props; other custom props stay hidden
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/un/angle", "/un/plain"], "all_changes": True}))
    await wait_for_capture_samples(started["id"], "/un/angle", "/un/plain")
    summary = j(await read_and_discard(client, {"capture_id": started["id"], "reduce": "latest"}))
    assert summary["data"]["/un/angle"]["unit"] == "deg" and "unit" not in summary["data"]["/un/plain"]
    del keep


async def test_meta_topics_are_listed_only_for_an_explicit_dollar_prefix(client, nt_server):
    plain = j(await client.call_tool("nt4_list_topics", {}))
    assert not any(name.startswith("$") for name in plain.get("topics", {}))
    meta = j(await client.call_tool("nt4_list_topics", {"prefix": "$"}))
    assert "$clients" in meta["topics"] and meta["topics"]["$clients"] == "msgpack"
    again = j(await client.call_tool("nt4_list_topics", {"prefix": "$"}))  # second call reuses the subscription
    assert again["topics"] == meta["topics"]


async def test_listing_can_show_struct_field_names_once_per_type(client, nt_server):
    keep = publish_schemas(nt_server)
    keep.append(publish(nt_server, "/sf/pose", "struct:Pose2d", ntcore.Value.makeRaw(struct.pack("<ddd", 1.0, 2.0, 0.5))))
    keep.append(publish(nt_server, "/sf/poses", "struct:Pose2d[]", ntcore.Value.makeRaw(struct.pack("<ddd", 1.0, 2.0, 0.5))))
    keep.append(publish(nt_server, "/sf/mystery", "struct:Nope", ntcore.Value.makeRaw(b"\x01")))
    keep.append(publish(nt_server, "/sf/x", "double", ntcore.Value.makeDouble(1.0)))
    await wait_for_client_sync(nt_server)
    plain = j(await client.call_tool("nt4_list_topics", {"prefix": "/sf/"}))
    assert "struct_fields" not in plain
    shown = j(await client.call_tool("nt4_list_topics", {"prefix": "/sf/", "struct_fields": True}))
    assert shown["struct_fields"] == {"struct:Pose2d": ["translation.x", "translation.y", "rotation.value"],
                                      "struct:Pose2d[]": ["translation.x", "translation.y", "rotation.value"]}
    del keep


async def test_inspect_accepts_several_targets_and_a_prefix(client, nt_server):
    keep = [publish(nt_server, "/mi/a", "double", ntcore.Value.makeDouble(1)),
            publish(nt_server, "/mi/b", "double", ntcore.Value.makeDouble(2)),
            publish(nt_server, "/other/c", "double", ntcore.Value.makeDouble(3))]
    await wait_for_client_sync(nt_server)
    several = j(await client.call_tool("nt4_inspect", {"what": "publishers", "target": ["/mi/a", "/other/c", "/mi/nope"]}))
    assert set(several["targets"]) == {"/mi/a", "/other/c", "/mi/nope"}
    assert [r["client"] for r in several["targets"]["/mi/a"]["rows"]] == ["(server)"]
    assert "err" in several["targets"]["/mi/nope"]  # one bad name does not fail the rest
    wildcard = j(await client.call_tool("nt4_inspect", {"what": "publishers", "target": "/mi/*"}))
    assert set(wildcard["targets"]) == {"/mi/a", "/mi/b"} and "omitted_targets" not in wildcard
    with pytest.raises(ToolError, match="No topics match"):
        await client.call_tool("nt4_inspect", {"what": "publishers", "target": "/zzz/*"})
    with pytest.raises(ToolError, match="only works for topic"):
        await client.call_tool("nt4_inspect", {"what": "client_subs", "target": "x*"})
    with pytest.raises(ToolError, match="not used"):
        await client.call_tool("nt4_inspect", {"what": "clients", "target": ["x"]})
    del keep


async def test_inspect_caps_the_number_of_targets(client, nt_server):
    keep = [publish(nt_server, f"/cap/t{i:02d}", "double", ntcore.Value.makeDouble(i)) for i in range(13)]
    await wait_for_client_sync(nt_server)
    capped = j(await client.call_tool("nt4_inspect", {"what": "publishers", "target": "/cap/*"}))
    assert len(capped["targets"]) == 10 and capped["omitted_targets"] == 3
    del keep


async def test_protobuf_values_decode_from_the_servers_descriptors(client, nt_server):
    from proto_helpers import encode, geometry_file

    file = geometry_file()
    _, pose = encode([file], "wpi.proto.ProtobufPose2d", translation={"x": 4.0, "y": 1.0}, rotation={"value": 0.5})
    keep = [publish(nt_server, "/.schema/proto:geometry2d.proto", "proto:FileDescriptorProto",
                    ntcore.Value.makeRaw(file.SerializeToString())),
            publish(nt_server, "/pb/pose", "proto:wpi.proto.ProtobufPose2d", ntcore.Value.makeRaw(pose.SerializeToString())),
            publish(nt_server, "/pb/mystery", "proto:nope.Missing", ntcore.Value.makeRaw(b""))]
    await wait_for_client_sync(nt_server)
    got = j(await client.call_tool("nt4_get_values", {"topics": ["/pb/pose", "/pb/mystery"]}))
    assert got["/pb/pose"]["v"] == {"translation.x": 4.0, "translation.y": 1.0, "rotation.value": 0.5}
    mystery = got["/pb/mystery"]["v"]  # no descriptor: hex, tagged with why it did not decode
    assert mystery["hex"] == "0801" and "no descriptor" in mystery["decode_error"]
    projected = j(await client.call_tool("nt4_get_values", {"topics": ["/pb/pose"], "fields": ["translation.x"]}))
    assert projected["/pb/pose"]["v"] == {"translation.x": 4.0}
    waited = j(await client.call_tool("nt4_wait_for", {"topic": "/pb/pose", "field": "translation.x", "op": ">", "value": 3, "wait_s": 2}))
    assert waited["met"] is True
    listed = j(await client.call_tool("nt4_list_topics", {"prefix": "/pb/", "struct_fields": True}))
    assert listed["struct_fields"] == {"proto:wpi.proto.ProtobufPose2d": ["translation.x", "translation.y", "rotation.value"]}
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/pb/pose"], "all_changes": True}))
    await wait_for_capture_samples(started["id"], "/pb/pose")
    summary = j(await read_and_discard(client, {"capture_id": started["id"], "reduce": "summary", "fields": ["translation.x"]}))
    assert summary["constants"]["/pb/pose"] == {"translation.x": 4.0}  # unchanging topics are listed compactly
    del keep


async def test_wait_for_every_condition_at_once(client, nt_server):
    amps = nt_server.getTopic("/wa/amps").genericPublish("double")
    mode = nt_server.getTopic("/wa/mode").genericPublish("string")
    amps.set(ntcore.Value.makeDouble(1.0))
    mode.set(ntcore.Value.makeString("disabled"))
    await wait_for_client_sync(nt_server)
    also = [{"topic": "/wa/mode", "op": "==", "value": "auto"}]

    async def drive():
        assert await wait_until(lambda: session.active_operations > 0)  # the wait call is in flight
        amps.set(ntcore.Value.makeDouble(50.0))  # a spike, but the robot is still disabled
        await asyncio.sleep(0.2)
        amps.set(ntcore.Value.makeDouble(1.0))
        mode.set(ntcore.Value.makeString("auto"))
        await asyncio.sleep(0.2)
        amps.set(ntcore.Value.makeDouble(60.0))  # a spike during auto

    task = asyncio.create_task(drive())
    r = j(await client.call_tool("nt4_wait_for", {"topic": "/wa/amps", "op": ">", "value": 40, "also": also, "wait_s": 5}))
    await task
    assert r["met"] is True and r["v"] == 60.0 and r["also"] == {"/wa/mode": "auto"}


async def test_wait_for_every_condition_times_out_when_one_never_holds(client, nt_server):
    amps = nt_server.getTopic("/wb/amps").genericPublish("double")
    mode = nt_server.getTopic("/wb/mode").genericPublish("string")
    amps.set(ntcore.Value.makeDouble(50.0))
    mode.set(ntcore.Value.makeString("disabled"))
    await wait_for_client_sync(nt_server)
    r = j(await client.call_tool("nt4_wait_for", {"topic": "/wb/amps", "op": ">", "value": 40, "wait_s": 0.5,
                                                  "also": [{"topic": "/wb/mode", "op": "==", "value": "auto"}]}))
    assert r["met"] is False and r["also"] == {"/wb/mode": "disabled"}
    with pytest.raises(ToolError, match="needs a `value`"):
        await client.call_tool("nt4_wait_for", {"topic": "/wb/amps", "op": ">", "value": 40, "wait_s": 0.5,
                                                "also": [{"topic": "/wb/mode", "op": "==", "value": None}]})

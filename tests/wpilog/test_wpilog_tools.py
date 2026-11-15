"""The log server through a real MCP client: what an assistant would call and see."""

import json

import pytest
from fastmcp.exceptions import ToolError

TOOLS = {"wpilog_list", "wpilog_open", "wpilog_close", "wpilog_topics", "wpilog_get_values", "wpilog_query",
         "wpilog_when", "wpilog_text", "wpilog_diff", "wpilog_sync"}


async def call(client, name, args=None):
    return json.loads((await client.call_tool(name, args or {})).content[0].text)


async def test_the_server_offers_exactly_the_documented_tools_and_all_are_read_only(client):
    tools = await client.list_tools()
    assert {t.name for t in tools} == TOOLS
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint and not t.annotations.open_world_hint for t in tools)


async def test_listing_and_opening_a_log(client):
    listing = await call(client, "wpilog_list")
    assert listing["total"] == 1 and listing["logs"][0]["file"] == "sample.wpilog"
    opened = await call(client, "wpilog_open", {"path": "sample.wpilog"})
    assert opened["log"] == "l1" and opened["duration_s"] == 12.0
    assert opened["enabled"] == [{"mode": "auto", "from_s": 2.0, "to_s": 5.0, "for_s": 3.0},
                                 {"mode": "teleop", "from_s": 6.0, "to_s": 10.0, "for_s": 4.0}]
    assert opened["match"] == {"EventName": "Practice Field", "MatchNumber": 12}
    assert opened["busiest"][0] == ["/Fake/Elevator/Current", 121] and "warnings" not in opened


async def test_listing_an_empty_folder_says_where_it_looked(client, monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("WPILOG_DIR", str(empty))
    out = await call(client, "wpilog_list")
    assert out["total"] == 0 and "WPILOG_DIR" in out["hint"]


async def test_topics_collapse_page_and_show_struct_fields(client):
    await call(client, "wpilog_open", {"path": "sample.wpilog"})
    top = await call(client, "wpilog_topics", {"depth": 1})
    assert top["groups"] == {"/Fake/": 6, "/FMSInfo/": 2} and top["hidden_matches"] == 3
    fields = await call(client, "wpilog_topics", {"prefix": "/Fake/Drive", "struct_fields": True, "counts": True})
    assert fields["topics"] == {"/Fake/Drive/Pose": "struct:Pose2d"}
    assert fields["struct_fields"] == {"struct:Pose2d": ["translation.x", "translation.y", "rotation.value"]}
    assert fields["props"] == {"/Fake/Drive/Pose": {"n": 31}}


async def test_values_query_when_and_text_answer_like_the_unit_tests(client):
    await call(client, "wpilog_open", {"path": "sample.wpilog"})
    values = await call(client, "wpilog_get_values", {"topics": ["/Fake/Elevator/Current"], "at_ms": 3200})
    assert values == {"/Fake/Elevator/Current": {"v": 42.5, "t_ms": 3200.0, "unit": "A"}}
    query = await call(client, "wpilog_query", {"topics": ["/Fake/Elevator/Current"], "reduce": "edges", "threshold": 40, "align": "auto"})
    assert query["t0_s"] == 2.0 and query["data"]["/Fake/Elevator/Current"]["rising_ms"] == [1000.0, 5000.0]
    when = await call(client, "wpilog_when", {"topic": "/Fake/Elevator/Current", "op": ">", "value": 40})
    assert when["n"] == 2 and when["total_ms"] == 700.0
    text = await call(client, "wpilog_text", {"name_contains": "error"})
    assert text["items"][0]["v"] == "ERROR: arm limit switch not found"


async def test_a_log_can_be_closed_and_then_nothing_is_open(client):
    await call(client, "wpilog_open", {"path": "sample.wpilog"})
    assert await call(client, "wpilog_close") == {"closed": "l1", "open": []}
    with pytest.raises(ToolError, match="No log is open"):
        await call(client, "wpilog_topics")
    with pytest.raises(ToolError, match="No log is open"):
        await call(client, "wpilog_close")


async def test_tools_that_need_a_log_say_so_before_one_is_open(client):
    for tool, args in (("wpilog_query", {"topics": ["/x"]}), ("wpilog_when", {"topic": "/x", "op": ">", "value": 1}),
                       ("wpilog_get_values", {"topics": ["/x"]}), ("wpilog_text", {})):
        with pytest.raises(ToolError, match="Call wpilog_list, then wpilog_open"):
            await call(client, tool, args)


async def test_bad_requests_become_clear_tool_errors(client):
    with pytest.raises(ToolError, match="Only .wpilog, .dslog and .dsevents files"):
        await call(client, "wpilog_open", {"path": "sample.txt"})
    with pytest.raises(ToolError, match="No log named"):
        await call(client, "wpilog_open", {"path": "missing.wpilog"})
    await call(client, "wpilog_open", {"path": "sample.wpilog"})
    with pytest.raises(ToolError, match="Enabled modes found"):
        await call(client, "wpilog_query", {"topics": ["/Fake/Arm/Angle"], "align": "test"})
    with pytest.raises(ToolError, match="not in this log"):
        await call(client, "wpilog_when", {"topic": "/nope", "op": ">", "value": 1})
    with pytest.raises(ToolError, match="No open log 'l7'"):
        await call(client, "wpilog_topics", {"log": "l7"})

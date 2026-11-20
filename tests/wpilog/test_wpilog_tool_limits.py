"""Size caps, multi-log selection and side-by-side comparison in the wpilog tool surface."""

import json

import pytest
from fastmcp.exceptions import ToolError
from test_wpilog_tools import call  # the shared call helper

from frc_mcp_wpilog.tools import file_tools

TOOLS = {"wpilog_list", "wpilog_open", "wpilog_close", "wpilog_topics", "wpilog_get_values", "wpilog_query",
         "wpilog_when", "wpilog_text", "wpilog_scan", "wpilog_diff", "wpilog_sync"}



async def test_the_size_cap_returns_a_valid_partial_result(client, monkeypatch):
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "500")
    await call(client, "wpilog_open", {"path": "sample.wpilog"})
    out = await call(client, "wpilog_query", {"topics": ["/Fake/*"], "reduce": "samples", "max_samples": 500})
    assert out["truncated"] is True and out["omitted_items"] > 0 and "partial" in out


async def test_two_logs_can_be_open_and_selected_by_id(client, log_dir):
    from log_builder import build_sample_log

    build_sample_log(log_dir / "second.wpilog")
    await call(client, "wpilog_open", {"path": "sample.wpilog"})
    second = await call(client, "wpilog_open", {"path": "second.wpilog"})
    assert second["log"] == "l2"
    assert (await call(client, "wpilog_topics", {"log": "l1", "prefix": "/Fake/Arm"}))["topics"] == {"/Fake/Arm/Angle": "double"}


async def test_query_compares_open_logs_side_by_side(client, log_dir):
    from log_builder import build_sample_log

    build_sample_log(log_dir / "second.wpilog")
    await call(client, "wpilog_open", {"path": "sample.wpilog"})
    await call(client, "wpilog_open", {"path": "second.wpilog"})
    out = await call(client, "wpilog_query", {"topics": ["/Fake/Elevator/Current"], "log": "l1", "compare_with": ["l2"],
                                              "reduce": "percentiles"})
    assert out["logs"] == {"l1": "sample.wpilog", "l2": "second.wpilog"}
    assert out["data"]["/Fake/Elevator/Current"]["l1"] == out["data"]["/Fake/Elevator/Current"]["l2"]
    newest = await call(client, "wpilog_query", {"topics": ["/Fake/Elevator/Current"], "compare_with": ["l1"]})
    assert list(newest["logs"]) == ["l2", "l1"]  # without `log`, the newest is the first of the logs compared
    with pytest.raises(ToolError, match="No open log 'l9'"):
        await call(client, "wpilog_query", {"topics": ["/x"], "compare_with": ["l9"]})
    with pytest.raises(ToolError, match="Give each log id once"):
        await call(client, "wpilog_query", {"topics": ["/x"], "log": "l1", "compare_with": ["l1"]})


async def test_struct_fields_are_listed_while_holding_the_shared_state_lock(client, monkeypatch):
    from frc_mcp_wpilog import logfile
    from frc_mcp_wpilog.tools import topic_tools

    held = []
    real = topic_tools.fields_by_type
    monkeypatch.setattr(topic_tools, "fields_by_type", lambda types: held.append(logfile.LOCK._is_owned()) or real(types))
    await call(client, "wpilog_open", {"path": "sample.wpilog"})
    await call(client, "wpilog_topics", {"prefix": "/Fake/Drive", "struct_fields": True})
    assert held == [True]  # another thread's query could otherwise swap the schema registries mid-listing


async def test_listing_nudges_to_set_wpilog_dir_only_when_it_is_unset(client, monkeypatch, log_dir):
    assert "hint" not in await call(client, "wpilog_list")  # WPILOG_DIR is set by the fixture
    monkeypatch.delenv("WPILOG_DIR")
    monkeypatch.chdir(log_dir)
    out = await call(client, "wpilog_list")
    assert out["total"] == 1 and "WPILOG_DIR is not set" in out["hint"]


async def test_opening_a_cut_short_log_warns(client, log_dir):
    data = (log_dir / "sample.wpilog").read_bytes()
    (log_dir / "cut.wpilog").write_bytes(data[:-3])
    opened = await call(client, "wpilog_open", {"path": "cut.wpilog"})
    assert any("middle of a record" in w for w in opened["warnings"])


async def test_the_file_tools_are_plain_functions_around_the_shared_store(log_dir):
    """Each file tool is a plain coroutine: the MCP layer only wraps it, so it can be called directly too."""
    listing = json.loads((await file_tools.wpilog_list()).content[0].text)
    assert listing["total"] == 1 and listing["logs"][0]["file"] == "sample.wpilog"
    opened = json.loads((await file_tools.wpilog_open("sample.wpilog")).content[0].text)
    assert opened["log"] == "l1" and opened["enabled"][0]["mode"] == "auto"
    closed = json.loads((await file_tools.wpilog_close()).content[0].text)
    assert closed == {"closed": "l1", "open": []}

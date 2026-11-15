"""The data tools called directly: each is a plain coroutine over `queries` plus the size-bounded reply."""

import json

from frc_mcp_wpilib.reduce.models import Condition
from frc_mcp_wpilog.store import logs
from frc_mcp_wpilog.tools import data_tools, file_tools

ELEVATOR = "/Fake/Elevator/Current"


def text(result):
    """The JSON payload of a tool reply, as an assistant would receive it."""
    return json.loads(result.content[0].text)


async def test_values_at_a_moment_reports_the_last_update(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    out = text(await data_tools.wpilog_get_values([ELEVATOR], at_ms=3200))
    assert out == {ELEVATOR: {"v": 42.5, "t_ms": 3200.0, "unit": "A"}}


async def test_query_reduces_one_topic(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    out = text(await data_tools.wpilog_query([ELEVATOR], reduce="edges", threshold=40, align="auto"))
    assert out["t0_s"] == 2.0 and out["data"][ELEVATOR]["rising_ms"] == [1000.0, 5000.0]


async def test_query_answers_for_several_logs_side_by_side(log_dir):
    from log_builder import build_sample_log

    build_sample_log(log_dir / "second.wpilog")
    await file_tools.wpilog_open("sample.wpilog")
    await file_tools.wpilog_open("second.wpilog")
    out = text(await data_tools.wpilog_query([ELEVATOR], log="l1", compare_with=["l2"], reduce="percentiles"))
    assert out["logs"] == {"l1": "sample.wpilog", "l2": "second.wpilog"}
    assert out["data"][ELEVATOR]["l1"] == out["data"][ELEVATOR]["l2"]


async def test_query_reports_the_bulk_it_had_to_omit(log_dir, monkeypatch):
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "500")
    await file_tools.wpilog_open("sample.wpilog")
    out = text(await data_tools.wpilog_query(["/Fake/*"], reduce="samples", max_samples=500))
    assert out["truncated"] is True and out["omitted_items"] > 0 and "partial" in out


async def test_when_lists_the_stretches_a_condition_held(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    out = text(await data_tools.wpilog_when(ELEVATOR, ">", 40))
    assert out["n"] == 2 and out["total_ms"] == 700.0


async def test_text_finds_the_messages_oldest_first(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    out = text(await data_tools.wpilog_text(name_contains="error"))
    assert out["items"][0]["v"] == "ERROR: arm limit switch not found"


async def test_a_tool_error_names_the_missing_log(log_dir):
    import pytest
    from fastmcp.exceptions import ToolError

    assert logs.ids == []
    with pytest.raises(ToolError, match="Call wpilog_list, then wpilog_open"):
        await data_tools.wpilog_when(ELEVATOR, ">", 40)


async def test_when_accepts_extra_conditions_as_plain_objects(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    also = [{"topic": "/Fake/Match/Mode", "op": "==", "value": "auto"}]
    out = text(await data_tools.wpilog_when(ELEVATOR, ">", 40, also=[Condition(**c) for c in also]))
    assert out["intervals_ms"] == [[3000.0, 3500.0]]

"""WPILOG_TOOLSET=core: the log server hides its rarely used tools to cut schema tokens, and puts them back on request."""

import pytest
from fastmcp import Client

from frc_mcp_wpilog import app
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.spec import SPEC

_, apply_toolset = SPEC.load()

FULL_TOOLS = {
    "wpilog_list", "wpilog_open", "wpilog_close", "wpilog_topics", "wpilog_get_values", "wpilog_query",
    "wpilog_when", "wpilog_text", "wpilog_scan", "wpilog_diff", "wpilog_sync",
}
FULL_ONLY_TOOLS = {"wpilog_diff", "wpilog_close", "wpilog_sync"}


async def listed() -> set[str]:
    async with Client(app.mcp) as c:
        return {t.name for t in await c.list_tools()}


async def test_full_only_tag_marks_exactly_diff_and_close():
    apply_toolset("full")
    assert {t.name for t in await app.mcp.list_tools() if app.FULL_ONLY_TAG in t.tags} == FULL_ONLY_TOOLS


async def test_apply_toolset_hides_and_restores_tools_and_instructions():
    try:
        assert apply_toolset("core") == "core"
        assert apply_toolset("core") == "core"  # repeating is harmless
        assert await listed() == FULL_TOOLS - FULL_ONLY_TOOLS
        assert app.mcp.instructions == app.server_instructions(core=True)
        assert "wpilog_diff" not in app.mcp.instructions
    finally:
        assert apply_toolset("full") == "full"
    assert await listed() == FULL_TOOLS
    assert "wpilog_diff" in app.mcp.instructions


def test_apply_toolset_reads_the_environment_only_when_called(monkeypatch):
    monkeypatch.setenv("WPILOG_TOOLSET", "everything")
    with pytest.raises(WPILogError, match="WPILOG_TOOLSET"):
        apply_toolset()
    with pytest.raises(WPILogError, match="WPILOG_TOOLSET"):
        apply_toolset("everything")
    monkeypatch.setenv("WPILOG_TOOLSET", "full")
    assert apply_toolset() == "full"

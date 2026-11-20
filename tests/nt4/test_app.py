"""The shared FastMCP app: annotations, hidden tools, schema slimming and error conversion."""

import pytest
from fastmcp import Client

from frc_mcp_nt4 import app
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.spec import SPEC, lifespan

_, apply_toolset = SPEC.load()

FULL_TOOLS = {
    "nt4_connection", "nt4_status", "nt4_list_topics", "nt4_inspect", "nt4_health", "nt4_get_values",
    "nt4_get_widgets", "nt4_wait_for", "nt4_capture_start", "nt4_capture_query", "nt4_capture_stop", "nt4_watch",
    "nt4_swerve", "nt4_scan", "nt4_set_value", "nt4_release_publisher",
}


FULL_ONLY_TOOLS = {"nt4_capture_start", "nt4_capture_query", "nt4_capture_stop", "nt4_set_value", "nt4_release_publisher"}


async def test_full_only_tag_marks_exactly_the_capture_and_write_tools():
    # Derived from the registered tools' tags, so a new or renamed tool tagged full-only is covered automatically.
    apply_toolset("full")
    tagged = {t.name for t in await app.mcp.list_tools() if app.FULL_ONLY_TAG in t.tags}
    assert tagged == FULL_ONLY_TOOLS


def test_annotation_presets_are_consistent():
    assert app.READ_ONLY["readOnlyHint"] is True
    assert app.LOCAL_STATE["readOnlyHint"] is False and app.LOCAL_STATE["destructiveHint"] is False
    assert app.WRITE["destructiveHint"] is True
    assert all(a["openWorldHint"] for a in (app.READ_ONLY, app.LOCAL_STATE, app.WRITE))


async def test_every_tool_is_registered_with_a_title_and_hints():
    async with Client(app.mcp) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert set(tools) == FULL_TOOLS
    assert all(t.annotations and t.annotations.title for t in tools.values())
    assert tools["nt4_get_values"].annotations.read_only_hint is True
    assert tools["nt4_set_value"].annotations.destructive_hint is True
    assert tools["nt4_capture_start"].annotations.destructive_hint is False


async def test_slim_tool_list_advertises_leaner_schema_than_validation_uses():
    full = (await app.mcp.get_tool("nt4_get_values")).parameters
    async with Client(app.mcp) as c:
        advertised = next(t for t in await c.list_tools() if t.name == "nt4_get_values").input_schema
    assert "additionalProperties" in full
    assert "additionalProperties" not in advertised
    assert len(str(advertised)) < len(str(full))


async def test_slim_tool_list_only_trims_the_watch_hint_in_core():
    async def passthrough(_context):
        return [await app.mcp.get_tool("nt4_get_values"), await app.mcp.get_tool("nt4_watch")]

    full = {t.name: t.description for t in await app.CoreAwareToolList(core=False).on_list_tools(None, passthrough)}
    core = {t.name: t.description for t in await app.CoreAwareToolList(core=True).on_list_tools(None, passthrough)}
    assert app.WATCH_HINT in full["nt4_watch"]
    assert app.WATCH_HINT not in core["nt4_watch"]
    assert core["nt4_get_values"] == full["nt4_get_values"]


async def test_apply_toolset_hides_and_restores_the_registry_and_instructions():
    try:
        assert apply_toolset("core") == "core"
        assert apply_toolset("core") == "core"  # repeating is harmless
        async with Client(app.mcp) as c:
            names = {t.name for t in await c.list_tools()}
        assert not FULL_ONLY_TOOLS & names and names == FULL_TOOLS - FULL_ONLY_TOOLS
        assert app.mcp.instructions == app.server_instructions(core=True)
    finally:
        assert apply_toolset("full") == "full"
    async with Client(app.mcp) as c:
        assert {t.name for t in await c.list_tools()} == FULL_TOOLS
    assert app.mcp.instructions == app.server_instructions(core=False)
    assert app.slim_tools.core is False


def test_apply_toolset_reads_the_environment_only_when_called(monkeypatch):
    monkeypatch.setenv("NT4_TOOLSET", "everything")
    with pytest.raises(NT4Error, match="NT4_TOOLSET"):
        apply_toolset()
    with pytest.raises(NT4Error, match="NT4_TOOLSET"):
        apply_toolset("everything")
    monkeypatch.setenv("NT4_TOOLSET", "full")
    assert apply_toolset() == "full"


def test_shared_parameter_types_are_annotated():
    for alias in (app.Fields, app.Topics, app.RateHz):
        assert hasattr(alias, "__metadata__")


async def test_lifespan_disconnects_on_exit(monkeypatch):
    calls = []

    class FakeSession:
        async def disconnect_safely(self, wait=False):
            calls.append(wait)

    monkeypatch.setattr("frc_mcp_nt4.session.session", FakeSession())  # the lifespan looks it up when it runs
    with pytest.raises(RuntimeError):
        async with lifespan(app.mcp):
            raise RuntimeError("boom")
    assert calls == [True]



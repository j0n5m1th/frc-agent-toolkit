"""`frc_mcp_core`: the setup every server shares (toolset setting, server factory, error wrapper, lean schemas, running over stdio)."""

import json
from typing import Annotated

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from pydantic import Field
from repo_layout import PACKAGE_DIRS

from frc_mcp_core.server import create_server, new_server
from frc_mcp_core.tools import SlimToolList, tool_errors_for
from frc_mcp_core.toolset import FULL_ONLY_TAG, Toolset


class Problem(Exception):
    pass


async def names(server) -> list[str]:
    async with Client(server) as client:
        return sorted(t.name for t in await client.list_tools())


def test_a_toolset_setting_reads_the_environment_now_and_names_itself_when_wrong(monkeypatch):
    setting = Toolset("T_SET", Problem)
    assert setting.current() == "full"
    monkeypatch.setenv("T_SET", " CORE ")
    assert setting.current() == "core" and setting.parse("") == "full"
    monkeypatch.setenv("T_SET", "tiny")
    with pytest.raises(Problem, match="T_SET must be 'full' or 'core', not 'tiny'"):
        setting.current()
    assert Toolset("T_SET", Problem, getenv={"T_SET": "core"}.get).current() == "core"  # a server's own layering of the setting


async def test_create_server_hides_the_full_only_tools_in_core_and_shows_them_again(monkeypatch):
    mcp, apply_toolset = create_server("t", "text", Toolset("T_SET", Problem))

    @mcp.tool()
    def always() -> int:
        return 1

    @mcp.tool(tags={FULL_ONLY_TAG})
    def extra() -> int:
        return 2

    assert apply_toolset("core") == "core" and await names(mcp) == ["always"]
    assert apply_toolset("full") == "full" and await names(mcp) == ["always", "extra"]
    monkeypatch.setenv("T_SET", "core")
    assert apply_toolset() == "core" and await names(mcp) == ["always"]  # the setting, read now
    with pytest.raises(Problem, match="T_SET must be"):
        apply_toolset("tiny")
    assert await names(mcp) == ["always"]  # a bad value changed nothing


def test_instructions_and_the_extra_hook_follow_the_toolset(monkeypatch):
    seen: list[bool] = []
    monkeypatch.setenv("T_SET", "core")
    mcp, apply_toolset = create_server("t", lambda core: "core text" if core else "full text", Toolset("T_SET", Problem), on_toolset=seen.append)
    assert mcp.instructions == "core text" and seen == [True]  # what it declares at creation comes from the setting
    apply_toolset("full")
    assert mcp.instructions == "full text" and seen == [True, False]


async def test_servers_advertise_lean_schemas_and_take_their_own_middleware():
    def probe(top: Annotated[int, Field(ge=1, le=50)] = 12, match: str | None = None) -> None: ...

    mcp = new_server("t", "text")
    mcp.add_tool(Tool.from_function(probe, name="probe"))
    async with Client(mcp) as client:
        [listed] = await client.list_tools()
    assert '"minimum"' not in json.dumps(listed.input_schema) and '"anyOf"' not in json.dumps(listed.input_schema)
    assert '"minimum": 1' in json.dumps(Tool.from_function(probe, name="probe").parameters)  # the schema used for validation does carry the bounds

    custom = SlimToolList()
    assert new_server("t", "text", slim=custom).middleware[-1] is custom


async def test_an_error_of_the_servers_own_type_reaches_the_model_as_written_and_others_do_not():
    @tool_errors_for(Problem)
    async def one() -> dict[str, int]:
        """doc"""
        raise Problem("exactly this wording")

    @tool_errors_for(Problem)
    async def other() -> int:
        raise KeyError("not wrapped")

    assert one.__doc__ == "doc" and one.__name__ == "one"
    with pytest.raises(ToolError, match="exactly this wording"):
        await one()
    with pytest.raises(KeyError):
        await other()


def test_every_server_is_declared_with_a_server_spec():
    """The setup is written once in frc_mcp_core: a server that hand-writes it again (or its own `check` command line) is how the duplication started."""
    problems = []
    for package, folder in sorted(PACKAGE_DIRS.items()):
        if folder.name.endswith("_common") or package == "frc_mcp_core" or not ((folder / "app.py").exists() or (folder / "spec.py").exists()):
            continue
        spec = folder / "spec.py"
        if not spec.exists() or "ServerSpec(" not in spec.read_text(encoding="utf-8"):
            problems.append(f"{package}/spec.py does not declare a ServerSpec")
        for leftover in ("cli.py", "server.py", "__main__.py"):
            if (folder / leftover).exists():
                problems.append(f"{package}/{leftover} is what the ServerSpec replaces")
        app = folder / "app.py"
        if app.exists():
            text = app.read_text(encoding="utf-8")
            if "SPEC.create(" not in text:
                problems.append(f"{package}/app.py builds its own FastMCP instance")
            if "def apply_toolset" in text:
                problems.append(f"{package}/app.py defines its own apply_toolset")
    assert problems == []


async def test_only_the_servers_own_errors_reach_the_model_through_fastmcp():
    """Through a real FastMCP call: a plain exception (a library error with paths or a password in it) is masked, our own error is not."""
    mcp = new_server("t", "text")

    @mcp.tool
    @tool_errors_for(Problem)
    async def own() -> int:
        raise Problem("exactly this wording")

    @mcp.tool
    @tool_errors_for(Problem)
    async def other() -> int:
        raise RuntimeError("secret pw=hunter2 /home/u/.ssh/id")

    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="exactly this wording"):
            await client.call_tool("own", {})
        with pytest.raises(ToolError) as masked:
            await client.call_tool("other", {})
    assert "hunter2" not in str(masked.value) and ".ssh" not in str(masked.value)


async def test_apply_toolset_after_the_gates_keeps_gated_tools_hidden():
    from frc_mcp_core.toolset import gate_tag, set_gate_visibility

    mcp, apply_toolset = create_server("t", "text", Toolset("T_SET", Problem))
    gate = gate_tag("T_ALLOW")

    @mcp.tool(tags={FULL_ONLY_TAG, gate})
    def gated() -> int:
        return 1

    @mcp.tool(tags={FULL_ONLY_TAG})
    def extra() -> int:
        return 2

    set_gate_visibility(mcp, {gate: False}, core=False)
    assert await names(mcp) == ["extra"]
    apply_toolset("full")
    assert await names(mcp) == ["extra"]
    apply_toolset("core")
    assert await names(mcp) == []
    set_gate_visibility(mcp, {gate: True}, core=False)
    apply_toolset("full")
    assert await names(mcp) == ["extra", "gated"]

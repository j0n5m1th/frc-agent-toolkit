"""A tool is tagged `gated` exactly when it always refuses while its switch is off, so it can be hidden (read from the source, no server starts)."""

import ast
import json
from pathlib import Path

import pytest
from fastmcp.tools import Tool
from gate_cases import CASES
from repo_layout import FRC

from frc_mcp_core.toolset import GATE_TAG_PREFIX, GATED_TAG, set_gate_visibility

TOOL_FILES = sorted(FRC.glob("servers/*/*/tools/*.py"))
REQUIRE_CALLS = {"require", "require_record"}
# Gated tools that do not call `.require()` themselves: nt4's write tools refuse inside `check_writable` (switch, prefixes and read-only together).
REFUSE_ELSEWHERE = {"nt4_set_value", "nt4_release_publisher"}
# Gate switches the plugin's settings dialog has no setting for: NT4 writes also need NT4_WRITE_PREFIXES, which the dialog cannot hold, so the plugin offers
# only the read-only switch (on by default) and a user who wants writes sets NT4_ALLOW_WRITES and the prefixes on the server themselves.
NOT_IN_DIALOG = {"NT4_ALLOW_WRITES"}


def is_require(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in REQUIRE_CALLS


def tools_in(path: Path):
    """(name, tag source, requires at the top level, requires anywhere) for each `@mcp.tool(...)` function in the file."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "tool":
                tags = next((ast.unparse(k.value) for k in decorator.keywords if k.arg == "tags"), "")
                top = any(isinstance(s, ast.Expr) and is_require(s.value) for s in node.body)
                anywhere = any(is_require(n) for n in ast.walk(node))
                yield node.name, tags, top, anywhere


ALL_TOOLS = [(path.name, *entry) for path in TOOL_FILES for entry in tools_in(path)]


def test_the_scan_found_the_tools():
    assert len(ALL_TOOLS) > 60
    assert {name for _f, name, _t, top, _a in ALL_TOOLS if top} >= {"wpilib_deploy", "jfr_record", "ctre_install", "caniv_flash", "systemcore_reboot"}


@pytest.mark.parametrize(("file", "name", "tags", "top", "anywhere"), ALL_TOOLS, ids=[f"{t[0]}::{t[1]}" for t in ALL_TOOLS])
def test_a_tool_is_tagged_gated_exactly_when_it_always_requires_its_switch(file, name, tags, top, anywhere):
    gated = "gated_by(" in tags
    if top:
        assert gated, f"{name} always calls require() but is not tagged with gated_by(...): it would be listed while it can only refuse"
    elif name in REFUSE_ELSEWHERE:
        assert gated
    else:
        assert not gated, f"{name} is tagged gated but does not call require() first (a conditional refusal must stay visible)"
    if anywhere and not top:
        assert not gated, f"{name} only requires its switch on one path: hiding it would remove the other"


async def test_every_gated_tool_names_a_switch_its_server_declares_and_the_plugin_sets_it():
    declared: set[str] = set()
    for key, (spec, _env, _hidden, _one) in CASES.items():
        mcp, _ = spec.load()
        set_gate_visibility(mcp, dict.fromkeys(spec.gates, True), core=False)
        tools: list[Tool] = list(await mcp.list_tools())
        gated = {t.name: {tag for tag in t.tags if tag.startswith(GATE_TAG_PREFIX)} for t in tools if GATED_TAG in t.tags}
        assert gated, key
        for name, tags in gated.items():
            assert len(tags) == 1 and tags <= set(spec.gates), f"{name}: {tags} is not one of {sorted(spec.gates)}"
        declared |= {tag.removeprefix(GATE_TAG_PREFIX) for tag in spec.gates}
        assert {t.name for t in tools if GATED_TAG in t.tags} == CASES[key][2], key
    servers = json.loads((FRC / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    in_plugin = {name for server in servers.values() for name in server["env"]}
    assert declared - in_plugin == NOT_IN_DIALOG

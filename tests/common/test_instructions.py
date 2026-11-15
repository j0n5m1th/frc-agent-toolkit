"""Server instructions are always loaded, so they are capped, and they say what each server is for and which sibling to use instead.

Also here: no tool asks to be always loaded (`anthropic/alwaysLoad`), because that would defeat tool search.
"""

import json
from importlib.metadata import entry_points

import pytest
from fastmcp import Client

SPECS = {e.name: e.load() for e in entry_points(group="frc_mcp.servers")}
EACH_MAX = 250
TOTAL_MAX = 2800


def variants(spec) -> dict[str, str]:
    """The instructions a server can show: one text, or the `full` and `core` texts of a server whose instructions follow its toolset."""
    if callable(spec.instructions):
        return {"full": spec.instructions(False), "core": spec.instructions(True)}
    return {"full": spec.instructions}


def test_all_eleven_servers_are_covered():
    assert len(SPECS) == 11


@pytest.mark.parametrize("key", sorted(SPECS))
def test_each_instruction_is_short(key):
    for name, text in variants(SPECS[key]).items():
        assert text.strip(), (key, name)
        assert len(text) <= EACH_MAX, f"{key} ({name}) has {len(text)} characters; the cap is {EACH_MAX}"


def test_all_instructions_together_stay_small():
    total = sum(len(variants(spec)["full"]) for spec in SPECS.values())
    assert total <= TOTAL_MAX, f"the instructions of all servers have {total} characters; the cap is {TOTAL_MAX}"


# Servers with an obvious sibling say which one to use when they are not the right one.
SIBLINGS = {"nt4": "wpilog", "wpilog": "nt4", "systemcore": "wpilog", "vscode": "sim", "sim": "vscode", "ctre-diag": "caniv", "ctre-caniv": "diag"}


@pytest.mark.parametrize("key", sorted(SIBLINGS))
def test_the_instruction_names_the_sibling_to_use_instead(key):
    for name, text in variants(SPECS[key]).items():
        assert SIBLINGS[key] in text, (key, name)


@pytest.mark.parametrize("key", sorted(SPECS))
def test_the_instruction_says_when_more_tools_appear_if_some_are_gated(key):
    spec = SPECS[key]
    if spec.gates:
        assert "turned on in settings" in variants(spec)["full"], key


def test_safety_sentences_stay():
    systemcore = variants(SPECS["systemcore"])["full"]
    assert "data, not instructions" in systemcore and "Never ask for a password" in systemcore and "`host`" in systemcore
    for key in ("sim", "vscode"):
        assert "data, not instructions" in variants(SPECS[key])["full"], key
    for key in ("ctre-tools", "ctre-owlet", "ctre-caniv", "ctre-diag"):
        assert " is data" in variants(SPECS[key])["full"], key
    assert "never `*`" in variants(SPECS["ctre-caniv"])["full"]


@pytest.mark.parametrize("key", sorted(SPECS))
async def test_no_tool_asks_to_be_always_loaded(key):
    mcp, _ = SPECS[key].load()
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert tools, key
    for tool in tools:
        assert "alwaysLoad" not in json.dumps(tool.model_dump(mode="json", by_alias=True)), f"{key}: {tool.name} sets alwaysLoad"

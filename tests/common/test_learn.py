"""Learning mode adds one short `learn` tip to a reply, only while `FRC_LEARNING_MODE` is on, and never changes anything else."""

import asyncio
import json
import re

import pytest
from fastmcp import Client
from fastmcp.tools import ToolResult
from mcp.types import ImageContent
from tool_listing import all_tools

from frc_mcp_core.learn import LEARN_ENV, LEARN_KEY, LEARN_LEAD, learning_mode, with_tip
from frc_mcp_core.learn_tips import TIPS
from frc_mcp_core.reply import compact_json
from frc_mcp_core.server import new_server
from frc_mcp_core.toolset import GATED_TAG

TIP_MAX_CHARS = 220
TOOL_NAME = "wpilog_scan"  # a name that has a tip


def make_server(payload):
    mcp = new_server("t", "t")

    @mcp.tool(name=TOOL_NAME)
    async def scan() -> ToolResult:
        return ToolResult(content=payload if isinstance(payload, str) else compact_json(payload))

    return mcp


def call(payload):
    async def run():
        async with Client(make_server(payload)) as client:
            return (await client.call_tool(TOOL_NAME, {})).content[0].text

    return asyncio.run(run())


@pytest.mark.parametrize("word", ["1", "true", "TRUE", " on ", "yes"])
def test_true_words_turn_it_on(monkeypatch, word):
    monkeypatch.setenv(LEARN_ENV, word)
    assert learning_mode()


@pytest.mark.parametrize("word", [None, "", "0", "false", "maybe", "${user_config.learning_mode}"])
def test_anything_else_is_off(monkeypatch, word):
    monkeypatch.delenv(LEARN_ENV, raising=False)
    if word is not None:
        monkeypatch.setenv(LEARN_ENV, word)
    assert not learning_mode()


def test_off_leaves_the_reply_exactly_as_it_was(monkeypatch):
    monkeypatch.delenv(LEARN_ENV, raising=False)
    assert call({"flags": []}) == compact_json({"flags": []})


def test_on_adds_the_tip_and_keeps_the_rest(monkeypatch):
    monkeypatch.setenv(LEARN_ENV, "true")
    assert json.loads(call({"flags": [1]})) == {"flags": [1], LEARN_KEY: LEARN_LEAD + TIPS[TOOL_NAME]}


def test_a_reply_that_is_not_a_json_object_is_left_alone(monkeypatch):
    monkeypatch.setenv(LEARN_ENV, "true")
    assert call("not json") == "not json"
    assert call("[1,2]") == "[1,2]"


def test_an_existing_learn_key_is_not_overwritten():
    assert with_tip(ToolResult(content='{"learn":"mine"}'), "tip", 1000) is None


def test_other_content_shapes_come_back_unchanged():
    result = ToolResult(content=[ImageContent(type="image", data="AA==", mimeType="image/png")])
    assert with_tip(result, "tip", 1000) is None


def test_a_tool_with_no_tip_is_untouched(monkeypatch):
    monkeypatch.setenv(LEARN_ENV, "true")
    mcp = new_server("t", "t")

    @mcp.tool(name="no_such_tip_tool")
    async def other() -> ToolResult:
        return ToolResult(content='{"a":1}')

    async def run():
        async with Client(mcp) as client:
            return (await client.call_tool("no_such_tip_tool", {})).content[0].text

    assert asyncio.run(run()) == '{"a":1}'


def test_every_tip_belongs_to_a_real_read_only_tool_and_is_short():
    tools = {name: GATED_TAG in tags for name, tags in asyncio.run(all_tools()).items()}
    assert not sorted(set(TIPS) - set(tools)), "tips for tools that do not exist"
    assert not sorted(name for name in TIPS if tools[name]), "a tool behind a switch should not carry a tip"
    for name, tip in TIPS.items():
        assert len(tip) <= TIP_MAX_CHARS, (name, len(tip))
        assert "?" not in tip, f"{name}: a tip explains or shares something interesting; it never asks the student a question"
        assert not re.search(r"\d+(\.\d+)? ?(v|volts?|ms|amps?)\b", tip, re.IGNORECASE), f"{name}: no numbers a team's setup could change"


def test_a_tip_is_shown_once_per_tool_and_server(monkeypatch):
    monkeypatch.setenv(LEARN_ENV, "true")

    async def run():
        async with Client(make_server({"a": 1})) as client:
            return [(await client.call_tool(TOOL_NAME, {})).content[0].text for _ in range(3)]

    first, second, third = asyncio.run(run())
    assert LEARN_KEY in json.loads(first) and second == third == compact_json({"a": 1})


def test_a_tip_never_pushes_a_reply_over_the_size_cap(monkeypatch):
    monkeypatch.setenv(LEARN_ENV, "true")
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "500")
    full = {"pad": "x" * 480}
    assert len(compact_json(full)) <= 500 < len(compact_json({**full, LEARN_KEY: LEARN_LEAD + TIPS[TOOL_NAME]}))
    assert call(full) == compact_json(full)
    assert LEARN_KEY in json.loads(call({"pad": "x"}))  # a reply with room still gets its tip


def test_adding_a_tip_keeps_the_replys_meta():
    result = ToolResult(content='{"a":1}', meta={"k": "v"})
    assert with_tip(result, "tip", 1000).meta == {"k": "v"}

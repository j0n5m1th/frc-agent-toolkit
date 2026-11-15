"""Agent lint: each subagent file in plugins/frc/agents has a name that matches its file, a description and an explicit tool list, and that list names only
tools that exist and only read (nothing the confirm-first hook covers, nothing that records or converts)."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from test_plugin_skills import all_tools

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "plugins" / "frc" / "agents"
AGENTS = sorted(AGENTS_DIR.glob("*.md"))
PREFIX = "mcp__plugin_frc-toolkit_"
WRITERS = {"jfr_record", "jfr_live", "owlet_convert"}  # record, run jcmd, write a file: never for a log reader


def frontmatter(path: Path) -> dict[str, str]:
    block = path.read_text(encoding="utf-8").split("---" + chr(10))[1]
    return dict(line.split(": ", 1) for line in block.splitlines())


def tool_list(path: Path) -> list[str]:
    return [name.strip() for name in frontmatter(path)["tools"].split(",")]


def plugin_tools(path: Path) -> list[tuple[str, str]]:
    """(server key, tool name) for each plugin tool the agent lists."""
    return [(match[1], match[2]) for name in tool_list(path) if (match := re.fullmatch(rf"{PREFIX}([a-z0-9-]+)__([a-z0-9_]+)", name))]


def confirm_first() -> re.Pattern[str]:
    hooks = json.loads((ROOT / "plugins" / "frc" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    return re.compile(hooks["hooks"]["PreToolUse"][0]["matcher"])


@pytest.fixture(scope="module")
def tools() -> dict[str, set[str]]:
    return asyncio.run(all_tools())


def test_the_plugin_has_agents():
    assert AGENTS


def test_every_agent_has_its_file_name_a_description_and_a_tool_list():
    for path in AGENTS:
        meta = frontmatter(path)
        assert meta["name"] == path.stem, path.name
        assert meta["description"].strip(), path.name
        assert len(meta["description"].split()) <= 34, path.name  # about 45 tokens, like a skill
        assert tool_list(path) and all(tool_list(path)), f"{path.name} needs an explicit tools list"


def test_every_plugin_tool_an_agent_lists_exists_on_the_server_it_names(tools):
    for path in AGENTS:
        for key, name in plugin_tools(path):
            assert name in tools, f"{path.name} lists {name}, which no server has"
            assert name.split("_")[0] in key.replace("-", "_").split("_") or key in {"ctre-owlet"}, f"{path.name}: {name} is not from {key}"


def test_the_tool_name_check_catches_a_made_up_name(tools):
    assert "wpilog_topics" in tools and "wpilog_no_such_tool" not in tools


def test_no_agent_lists_a_tool_that_writes_records_or_is_gated(tools):
    asks = confirm_first()
    for path in AGENTS:
        for name in tool_list(path):
            assert not asks.match(name), f"{path.name} lists {name}, which the confirm-first hook covers"
        for _, name in plugin_tools(path):
            assert name not in WRITERS, f"{path.name} lists {name}"
            assert "gated" not in tools.get(name, set()), f"{path.name} lists {name}, which is behind an *_ALLOW_* setting"


def test_the_hook_check_would_catch_a_tool_that_changes_things():
    assert confirm_first().match(f"{PREFIX}jfr__jfr_record") and not confirm_first().match(f"{PREFIX}jfr__jfr_summary")

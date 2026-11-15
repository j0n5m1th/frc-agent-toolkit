"""Skill lint: what a skill says must match what the plugin's servers really offer. Tool names a skill mentions exist, files it names exist, and a skill
that names a tool hidden behind a switch says which setting to turn on (with the switch off the tool is absent, not refused)."""

import asyncio
import re
from pathlib import Path

import pytest
from fastmcp import Client

from frc_mcp_core import command
from frc_mcp_core.toolset import GATED_TAG, set_gate_visibility

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "plugins" / "frc" / "skills"
SKILLS = sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir())
TOOL_LIKE = re.compile(r"\b(?:nt4|wpilog|sim|wpilib|systemcore|jfr|ctre|owlet|passerine|caniv|diag)_[a-z0-9_]+\b")
FULL_NAME = re.compile(r"\bmcp__plugin_frc-toolkit_[\w-]+?__([a-z0-9_]+)\b")
NOT_TOOLS: set[str] = set()  # lowercase words that look like a tool name but are not one (none yet); say why next to each
PATH = re.compile(r"`((?:references|scripts|assets)/[^`\s]+)`")
SETTING_WORDS = ("Allow", "settings", "/config")


def skill_texts(skill: Path) -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in sorted(skill.rglob("*")) if path.is_file() and path.suffix == ".md"}


async def all_tools() -> dict[str, set[str]]:
    """Tool name -> tags, over every server with every switch on and the full toolset. Visibility is left all-on, as `spec.load()` hands it out."""
    found: dict[str, set[str]] = {}
    for key in command.server_keys():
        spec = command.load_spec(key)
        mcp, _ = spec.load()
        set_gate_visibility(mcp, dict.fromkeys(spec.gates, True), core=False)
        async with Client(mcp) as client:
            for tool in await client.list_tools():
                found[tool.name] = set(tool.meta.get("fastmcp", {}).get("tags", []) if tool.meta else [])
    return found


@pytest.fixture(scope="module")
def tools() -> dict[str, set[str]]:
    return asyncio.run(all_tools())


def test_every_tool_name_a_skill_mentions_exists(tools):
    for skill in SKILLS:
        for path, text in skill_texts(skill).items():
            names = set(TOOL_LIKE.findall(text)) | set(FULL_NAME.findall(text))
            unknown = sorted(name for name in names - NOT_TOOLS if name not in tools)
            assert not unknown, f"{skill.name} ({path.name}) names tools no server has: {unknown}"


def test_the_tool_name_check_catches_a_made_up_name(tools):
    assert "nt4_set_value" in tools and "wpilib_no_such_tool" not in tools


def test_every_path_a_skill_names_exists_inside_its_folder():
    for skill in SKILLS:
        for path, text in skill_texts(skill).items():
            for named in PATH.findall(text):
                assert (skill / named).is_file(), f"{skill.name} ({path.name}) names {named}, which is not in the skill folder"


def test_no_skill_tells_the_model_to_start_a_skill_it_may_not_start():
    hidden = {skill.name for skill in SKILLS if "disable-model-invocation: true" in (skill / "SKILL.md").read_text(encoding="utf-8")}
    for skill in SKILLS:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        for name in sorted(hidden - {skill.name}):
            # naming it as a pointer for the user is fine; telling the model to invoke or run it is not
            assert not re.search(rf"\b(?:invoke|run|start|call|load|use the)\s+`?{re.escape(name)}\b", text, re.IGNORECASE), f"{skill.name} tells the model to start {name}"


def test_a_skill_that_names_a_gated_tool_says_which_setting_to_turn_on(tools):
    gated = {name for name, tags in tools.items() if GATED_TAG in tags}
    assert gated  # the check is empty without them
    for skill in SKILLS:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        named = sorted(set(TOOL_LIKE.findall(text)) & gated)
        if named:
            assert any(word in text for word in SETTING_WORDS), f"{skill.name} names {named}, which are absent while their switch is off, but never says which setting to turn on"
            if "/config" in text:  # a changed setting is read only when the server starts
                assert "reconnect" in text and "restart Claude Code" in text, f"{skill.name} sends the user to /config but not to reconnect the server"


def skill_text(name: str) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


def test_live_check_says_nt4_writes_need_the_users_own_server_entry():
    text = skill_text("wpilib-live-check")
    assert "NT4_ALLOW_WRITES" in text and "NT4_WRITE_PREFIXES" in text and "own server entry" in text
    assert 'turn off "NetworkTables read-only"' not in text  # that alone never enabled writes
    assert re.search(r"`log=true` on `nt4_capture_start`", text) and "`nt4_capture_query` (`log=true`" not in text


def test_provision_skill_gives_a_runnable_command_and_the_real_env_location():
    text = skill_text("systemcore-provision")
    assert "~/.mcp-systemcore/.env" in text and "in the server's folder" not in text
    assert "uv run --project \"${CLAUDE_PLUGIN_ROOT}\" --frozen --no-dev --python 3.13 frc-mcp systemcore trust <host>" in text
    assert "<owner>" not in text and "<tag>" not in text and "uvx" not in text  # nothing for the model to guess


def test_skills_pointing_to_a_gated_skill_give_the_slash_command():
    gated = {skill.name for skill in SKILLS if "disable-model-invocation: true" in skill_text(skill.name)}
    for skill in SKILLS:
        text = skill_text(skill.name)
        for name in gated - {skill.name}:
            for found in re.finditer(rf"(?<![\w-]){re.escape(name)}", text):
                assert text[max(0, found.start() - 5) : found.start()].endswith("/frc-toolkit:"), f"{skill.name} names {name} without /frc:{name}"


def needs_line(name: str) -> str:
    return next(line for line in skill_text(name).splitlines() if line.startswith("Needs "))


def test_no_skill_asks_for_a_server_it_does_not_use():
    assert "`sim` only" in needs_line("wpilib-live-check")  # a real robot needs no simulator
    assert "`sim` only" in needs_line("wpilib-profile")  # any JVM needs no simulator
    assert "MIDI" in needs_line("ctre-convert")  # hoot and MIDI need different servers

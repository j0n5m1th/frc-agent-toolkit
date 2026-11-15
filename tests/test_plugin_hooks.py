"""The plugin's two hooks (plugins/frc/hooks/hooks.json): a quiet `uv sync` at session start so the first server start is not slow, and a PreToolUse
hook that makes Claude Code ask a person before any tool that changes the robot, a device or this computer."""

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from frc_mcp_core import command
from frc_mcp_core.toolset import GATED_TAG, set_gate_visibility

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "frc"
HOOKS = PLUGIN / "hooks"
SHELL_CHARS = set("&|;<>`()*?!~#\"'\\")  # no shell runs this command, so none of these may be needed


def config() -> dict:
    return json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))["hooks"]


def handler() -> dict:
    (entry,) = config()["SessionStart"]
    (hook,) = entry["hooks"]
    return hook


def mcp_flags() -> list[str]:
    servers = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    return next(iter(servers.values()))["args"][1:-2]  # after `run`, before `frc-mcp <key>`


def test_hooks_folder_holds_only_hooks_json_and_it_is_valid():
    assert [path.name for path in HOOKS.iterdir()] == ["hooks.json"]
    assert isinstance(json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8")), dict)


def test_only_session_start_and_pre_tool_use_command_hooks():
    assert list(config()) == ["SessionStart", "PreToolUse"]
    assert handler()["type"] == "command"
    assert all(hook["type"] == "command" for event in config().values() for entry in event for hook in entry["hooks"])
    assert "\"prompt\"" not in (HOOKS / "hooks.json").read_text(encoding="utf-8")  # no prompt-type hooks


def test_exec_form_runs_uv_sync_quietly_with_the_same_flags_as_the_servers():
    hook = handler()
    assert hook["command"] == "uv" and hook["args"][0] == "sync"
    assert "--quiet" in hook["args"] and "${CLAUDE_PLUGIN_ROOT}" in hook["args"]
    flags = [arg for arg in hook["args"][1:] if arg != "--quiet"]
    assert flags == mcp_flags()  # same --project --frozen --no-dev --python 3.13, so `uv run` finds nothing to do
    assert "shell" not in hook and "async" not in hook


def test_no_shell_syntax_anywhere_in_the_command():
    hook = handler()
    for text in [hook["command"], *hook["args"]]:
        assert not (set(text) & SHELL_CHARS), text
        assert " " not in text and chr(10) not in text, text
    assert hook["command"] == "uv"


def test_timeout_is_generous_but_not_endless():
    assert 60 <= handler()["timeout"] <= 600


# ---- PreToolUse confirmation hook ----

# Every tool that changes something and is not ordinary project work: the 11 tagged `gated`, plus seven with no switch of their own (logs_prune is dry-run unless the
# switch is on; bundle makes the robot controller work for about 8 seconds, so it must not run during a match; connect and disconnect change this computer's Wi-Fi; wpilib_vendordep and wpilib_set_team edit project files; jfr_live pauses a running program).
# Not listed on purpose: sim_start, wpilib_run and wpilib_tool run the project's own fixed Gradle commands, and their boundary is the project-folder allowlist.
CONFIRM = {"nt4_set_value", "nt4_release_publisher", "wpilib_deploy", "wpilib_vendordep", "wpilib_set_team", "jfr_record", "jfr_live", "ctre_install", "caniv_configure", "caniv_flash", "systemcore_restart_robot_code",
           "systemcore_reboot", "systemcore_provision", "systemcore_ssh_forget", "systemcore_logs_prune", "systemcore_bundle", "systemcore_connect", "systemcore_disconnect"}
PREFIX = "mcp__plugin_frc-toolkit_"


def pre_tool_entry() -> dict:
    (entry,) = config()["PreToolUse"]
    return entry


def pre_tool_hook() -> dict:
    (hook,) = pre_tool_entry()["hooks"]
    return hook


async def plugin_tools() -> dict[str, list[tuple[str, set[str]]]]:
    """Server key -> (tool name, tags) of every tool, with every switch on and the full toolset (what the hook has to cover)."""
    found: dict[str, list[tuple[str, set[str]]]] = {}
    for key in command.server_keys():
        spec = command.load_spec(key)
        mcp, _ = spec.load()
        set_gate_visibility(mcp, dict.fromkeys(spec.gates, True), core=False)
        async with Client(mcp) as client:
            found[key] = [(tool.name, set(tool.meta.get("fastmcp", {}).get("tags", []) if tool.meta else [])) for tool in await client.list_tools()]
    return found


@pytest.fixture(scope="module")
def tools() -> dict[str, list[tuple[str, set[str]]]]:
    return asyncio.run(plugin_tools())


def test_the_server_keys_are_the_real_ones(tools):
    assert set(tools) == {"nt4", "wpilog", "sim", "vscode", "systemcore", "jfr", "ctre-tools", "ctre-owlet", "ctre-passerine", "ctre-caniv", "ctre-diag"}
    assert set(tools) == set(json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"])


def test_the_matcher_is_an_anchored_regex_over_the_full_plugin_tool_names():
    matcher = pre_tool_entry()["matcher"]
    assert re.fullmatch(r"[A-Za-z0-9_\-| ,]+", matcher) is None  # a plain list would be compared as exact names: a pattern needs regex characters
    assert matcher.startswith("^" + PREFIX) and matcher.endswith(")$")  # Claude Code's regex is not anchored on its own
    re.compile(matcher)


def test_the_matcher_matches_every_confirm_tool_under_its_real_name_and_nothing_else(tools):
    matcher = re.compile(pre_tool_entry()["matcher"])
    names = {key: [name for name, _tags in found] for key, found in tools.items()}
    assert {name for found in names.values() for name in found} >= CONFIRM, "a name in CONFIRM is no tool any more"
    for key, found in names.items():
        for name in found:
            assert bool(matcher.search(f"{PREFIX}{key}__{name}")) == (name in CONFIRM), f"{key}/{name}"
    for text in ("nt4_set_value", f"{PREFIX}nt4__nt4_set_value_more", "mcp__other_nt4__nt4_set_value", f"x{PREFIX}nt4__nt4_set_value", "Bash"):
        assert not matcher.search(text), text


def test_every_gated_tool_and_every_tool_that_is_not_read_only_work_is_covered(tools):
    gated = {name for found in tools.values() for name, tags in found if GATED_TAG in tags}
    assert len(gated) == 11 and gated <= CONFIRM
    assert CONFIRM - gated == {"systemcore_logs_prune", "systemcore_bundle", "systemcore_connect", "systemcore_disconnect", "wpilib_vendordep", "wpilib_set_team", "jfr_live"}


def test_read_only_tools_never_match(tools):
    matcher = re.compile(pre_tool_entry()["matcher"])
    quiet = [f"{PREFIX}{key}__{name}" for key, found in tools.items() for name, _tags in found
             if name.split("_", 1)[1] in {"status", "versions", "list", "read", "query", "summary", "info", "check"} or name in {"nt4_watch", "nt4_connection"}]
    assert quiet and not any(matcher.search(name) for name in quiet)


def test_the_pre_tool_use_hook_asks_in_exec_form_with_the_same_flags_as_the_servers():
    hook = pre_tool_hook()
    assert hook["command"] == "uv" and hook["args"][0] == "run"
    assert hook["args"][1:-4] == mcp_flags()  # same --project --frozen --no-dev --python 3.13 as the servers, so the environment is shared
    assert hook["args"][-4:-1] == ["frc-mcp", "hook", "ask"]
    assert "shell" not in hook and "async" not in hook and "if" not in hook
    assert 5 <= hook["timeout"] <= 60
    assert hook["onFailure"] == "block"  # Claude Code v2.1.295+: a hook that cannot start, times out or errors blocks the call instead of letting it through
    for text in [hook["command"], *hook["args"][:-1]]:
        assert not (set(text) & SHELL_CHARS) and " " not in text, text


def test_the_command_prints_one_valid_ask_line_with_a_one_line_reason():
    reason = pre_tool_hook()["args"][-1]
    assert chr(10) not in reason and 10 < len(reason) < 120
    out = subprocess.run([sys.executable, "-c", "import sys; from frc_mcp_core.command import main; sys.exit(main(sys.argv[1:]))", "hook", "ask", reason],
                         capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60, check=True).stdout
    assert out.count(chr(10)) == 1
    assert json.loads(out) == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": reason}}


def test_the_command_refuses_anything_but_ask_with_one_reason(capsys):
    for bad in ([], ["allow", "x"], ["ask"], ["ask", ""], ["ask", "a" + chr(10) + "b"], ["ask", "a", "b"]):
        assert command.main(["hook", *bad]) == 2
    assert capsys.readouterr().out == ""

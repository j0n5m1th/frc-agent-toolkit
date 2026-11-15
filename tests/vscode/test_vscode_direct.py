"""The VS Code command tools and the prompt driven directly, without an MCP client."""

import json

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_vscode import runner
from frc_mcp_vscode.prompts import check_build
from frc_mcp_vscode.tools.edit_tools import wpilib_set_team, wpilib_vendordep
from frc_mcp_vscode.tools.info_tools import wpilib_info
from frc_mcp_vscode.tools.run_tools import wpilib_deploy, wpilib_run, wpilib_tool


def text(result):
    return json.loads(result.content[0].text)


async def test_info_and_a_build_answer_when_called_directly(project):
    info = text(await wpilib_info())
    assert info["team"] == 9999 and info["year"] == "2027_alpha7" and info["deploy_allowed"] is False
    assert info["installed"] == {"CommandsV2": "1.0.0"}
    built = text(await wpilib_run("build"))
    assert built["ok"] is True and built["build"] == "BUILD SUCCESSFUL in 1s"
    assert (project / "args.log").read_text(encoding="utf-8").splitlines() == ["build --console=plain"]


async def test_tasks_can_be_filtered_and_a_failure_lists_its_lines(project):
    tasks = text(await wpilib_run("tasks", "glass"))
    assert tasks["tasks"] == ["Glass - Run the tool Glass"]
    (project / "fail.flag").write_text("", encoding="utf-8")
    failed = text(await wpilib_run("test"))
    assert failed["ok"] is False and failed["exit_code"] == 1 and failed["warnings"] == 1
    assert any("cannot find symbol" in line for line in failed["errors"])


async def test_the_edits_write_the_files_the_extension_would(project):
    renamed = text(await wpilib_set_team(9998))
    assert renamed == {"team": 9998, "was": 9999}
    prefs = json.loads((project / ".wpilib" / "wpilib_preferences.json").read_text(encoding="utf-8"))
    assert prefs["teamNumber"] == 9998 and prefs["projectYear"] == "2027_alpha7"
    with pytest.raises(ToolError, match="Team number"):
        await wpilib_set_team(0)
    installed = text(await wpilib_vendordep("install", "AprilTag"))
    assert installed == {"name": "AprilTag", "version": "2.0.0", "replaced": None}
    removed = text(await wpilib_vendordep("remove", "AprilTag"))
    assert removed == {"name": "AprilTag", "removed": "2.0.0"} and not (project / "vendordeps" / "AprilTag.json").exists()


async def test_a_tool_is_started_once_and_left_running(project):
    runner.TOOL_WATCH_S = 0.3
    try:
        first = text(await wpilib_tool("Glass"))
        again = text(await wpilib_tool("Glass"))
    finally:
        runner.TOOL_WATCH_S = 5.0
    assert first["ok"] is True and first["running"] is True and first["pid"]
    assert again["pid"] == first["pid"] and again["hint"] == "Glass is already running."
    assert (project / "args.log").read_text(encoding="utf-8").splitlines() == ["Glass --console=plain"]


async def test_deploy_needs_the_flag_and_then_carries_the_team(project, monkeypatch):
    with pytest.raises(ToolError, match="VSCODE_ALLOW_DEPLOY=1"):
        await wpilib_deploy()
    monkeypatch.setenv("VSCODE_ALLOW_DEPLOY", "1")
    out = text(await wpilib_deploy(False, False, False, 9998, None))
    assert out["ok"] is True and out["team"] == 9998
    assert (project / "args.log").read_text(encoding="utf-8").splitlines()[-1] == "deploy -PteamNumber=9998 --console=plain"


def test_the_build_prompt_reads_the_two_commands_in_order():
    text_ = check_build()
    assert "command='build'" in text_ and "command='test'" in text_
    assert "which file and line" in text_ and "do not edit anything until asked" in text_


async def test_the_prompt_is_offered_to_the_client(client):
    assert {p.name for p in await client.list_prompts()} == {"check_build"}
    prompt = await client.get_prompt("check_build", {})
    assert "BUILD" in prompt.messages[0].content.text or "build" in prompt.messages[0].content.text

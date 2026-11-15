"""The VS Code command tools end to end: a real MCP client drives a fake robot project and sees the JSON a model would."""

import json

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_vscode import app
from frc_mcp_vscode.errors import VSCodeError
from frc_mcp_vscode.spec import SPEC

_, apply_toolset = SPEC.load()


async def call(client, name, args=None):
    result = await client.call_tool(name, args or {})
    return json.loads(result.content[0].text)


def logged(project):
    return (project / "args.log").read_text(encoding="utf-8").splitlines()


async def test_info_reads_the_files_the_extension_reads(client):
    out = await call(client, "wpilib_info")
    assert out["team"] == 9999 and out["year"] == "2027_alpha7" and out["gradlerio"] == "2027.0.0-alpha-7" and out["java"] == 25
    assert out["deploy_targets"] == ["systemcore"] and out["deploy_allowed"] is False
    assert out["installed"] == {"CommandsV2": "1.0.0"}
    assert out["offline_available"] == {"CommandsV3": "1.0.0", "AprilTag": "2.0.0"} and out["offline_differs"] == {"CommandsV2": "1.0.0 -> 1.0.1"}


async def test_build_reports_ok_and_the_build_line(client, project):
    out = await call(client, "wpilib_run", {"command": "build"})
    assert out["ok"] is True and out["build"] == "BUILD SUCCESSFUL in 1s" and "errors" not in out
    assert logged(project) == ["build --console=plain"]


async def test_gradle_runs_with_the_jdk_the_wpilib_install_has_for_the_projects_year(client, project, tmp_path):
    info = await call(client, "wpilib_info")
    jdk = tmp_path / "wpilib" / "2027_alpha7" / "jdk"
    assert info["jdk"] == {"home": str(jdk), "from": "WPILib install"}
    await call(client, "wpilib_run", {"command": "build"})
    assert (project / "java_home.log").read_text(encoding="utf-8").strip() == str(jdk)


async def test_an_explicit_jdk_wins_and_a_bad_one_is_an_error(client, project, tmp_path, monkeypatch):
    from vscode_helpers import fake_java

    mine = fake_java(tmp_path / "my-jdk")
    monkeypatch.setenv("WPILIB_JAVA_HOME", str(mine))
    assert (await call(client, "wpilib_info"))["jdk"] == {"home": str(mine), "from": "WPILIB_JAVA_HOME"}
    await call(client, "wpilib_run", {"command": "clean"})
    assert (project / "java_home.log").read_text(encoding="utf-8").strip() == str(mine)
    monkeypatch.setenv("WPILIB_JAVA_HOME", str(tmp_path / "nope"))
    with pytest.raises(ToolError, match="WPILIB_JAVA_HOME.*no bin/java"):
        await call(client, "wpilib_run", {"command": "build"})


async def test_without_an_install_jdk_the_environment_is_left_alone(client, project, monkeypatch, tmp_path):
    monkeypatch.setenv("WPILIB_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("JAVA_HOME", "/inherited/jdk")
    assert (await call(client, "wpilib_info"))["jdk"] == "JAVA_HOME or PATH"
    await call(client, "wpilib_run", {"command": "build"})
    assert (project / "java_home.log").read_text(encoding="utf-8").strip() == "/inherited/jdk"


async def test_a_failing_build_returns_the_error_lines_and_warning_count(client, project):
    (project / "fail.flag").write_text("", encoding="utf-8")
    out = await call(client, "wpilib_run", {"command": "build"})
    assert out["ok"] is False and out["exit_code"] == 1 and out["warnings"] == 1 and out["build"].startswith("BUILD FAILED")
    assert any("cannot find symbol" in line for line in out["errors"])


async def test_test_results_are_summarized_and_offline_is_passed_on(client, project):
    out = await call(client, "wpilib_run", {"command": "test", "offline": True})
    assert any("3 tests completed, 1 failed" in line for line in out["tests"])
    assert logged(project) == ["test --offline --console=plain"]


async def test_tasks_can_be_filtered(client):
    out = await call(client, "wpilib_run", {"command": "tasks", "name_contains": "glass"})
    assert out["tasks"] == ["Glass - Run the tool Glass"]


async def test_deploy_is_refused_until_it_is_allowed(client, project):
    with pytest.raises(ToolError, match="VSCODE_ALLOW_DEPLOY=1"):
        await call(client, "wpilib_deploy")
    assert not (project / "args.log").exists()


async def test_a_garbled_deploy_flag_is_an_error_not_silently_off(client, monkeypatch):
    monkeypatch.setenv("VSCODE_ALLOW_DEPLOY", "maybe")
    with pytest.raises(ToolError, match="VSCODE_ALLOW_DEPLOY must be a boolean"):
        await call(client, "wpilib_deploy")


async def test_deploy_uses_the_team_from_the_preferences_and_the_flags_asked_for(client, project, monkeypatch):
    monkeypatch.setenv("VSCODE_ALLOW_DEPLOY", "1")
    out = await call(client, "wpilib_deploy", {"debug": True, "skip_tests": True})
    assert out["ok"] is True and out["team"] == 9999 and out["debug"] is True
    assert logged(project) == ["deploy -PteamNumber=9999 -PdebugMode -xcheck --console=plain"]
    await call(client, "wpilib_deploy", {"team": 9998})
    assert logged(project)[-1] == "deploy -PteamNumber=9998 --console=plain"
    with pytest.raises(ToolError, match="Team number"):
        await call(client, "wpilib_deploy", {"team": 99999})


async def test_a_tool_is_left_running_and_not_started_twice(client, project):
    from frc_mcp_vscode import runner

    runner.TOOL_WATCH_S = 1.5
    first = await call(client, "wpilib_tool", {"name": "Glass"})
    assert first["ok"] is True and first["running"] is True and first["pid"]
    again = await call(client, "wpilib_tool", {"name": "Glass"})
    assert again["pid"] == first["pid"] and "already running" in again["hint"]
    assert logged(project) == ["Glass --console=plain"]
    with pytest.raises(ToolError):
        await call(client, "wpilib_tool", {"name": "Evil"})


async def test_set_team_keeps_the_other_preferences(client, project):
    out = await call(client, "wpilib_set_team", {"team": 9998})
    assert out == {"team": 9998, "was": 9999}
    prefs = json.loads((project / ".wpilib" / "wpilib_preferences.json").read_text(encoding="utf-8"))
    assert prefs["teamNumber"] == 9998 and prefs["projectYear"] == "2027_alpha7"
    with pytest.raises(ToolError, match="Team number"):
        await call(client, "wpilib_set_team", {"team": 0})


async def test_vendordep_install_replaces_an_older_copy_and_remove_deletes_it(client, project):
    updated = await call(client, "wpilib_vendordep", {"action": "install", "name": "CommandsV2"})
    assert updated == {"name": "CommandsV2", "version": "1.0.1", "replaced": "1.0.0"}
    added = await call(client, "wpilib_vendordep", {"action": "install", "name": "AprilTag"})
    assert added["replaced"] is None and (project / "vendordeps" / "AprilTag.json").is_file()
    removed = await call(client, "wpilib_vendordep", {"action": "remove", "name": "AprilTag"})
    assert removed == {"name": "AprilTag", "removed": "2.0.0"} and not (project / "vendordeps" / "AprilTag.json").exists()
    with pytest.raises(ToolError, match="not in vendordeps"):
        await call(client, "wpilib_vendordep", {"action": "remove", "name": "AprilTag"})


async def test_a_conflicting_vendordep_is_refused(client):
    with pytest.raises(ToolError, match="conflicts with the installed CommandsV2"):
        await call(client, "wpilib_vendordep", {"action": "install", "name": "CommandsV3"})


@pytest.mark.parametrize("name", ["../x", "a/b", "a b", "", ".hidden", "x" * 90])
async def test_vendordep_names_cannot_escape_the_folder(client, name):
    with pytest.raises(ToolError):
        await call(client, "wpilib_vendordep", {"action": "install", "name": name})


async def test_a_missing_offline_vendordep_lists_what_exists(client):
    with pytest.raises(ToolError, match="have: .*AprilTag"):
        await call(client, "wpilib_vendordep", {"action": "install", "name": "Nope"})


async def test_the_command_is_limited_to_the_fixed_list(client):
    with pytest.raises(ToolError):
        await call(client, "wpilib_run", {"command": "deploy"})


async def test_core_toolset_hides_the_rarely_used_tools_and_restores_them():
    full = {"wpilib_info", "wpilib_run", "wpilib_deploy", "wpilib_tool", "wpilib_vendordep", "wpilib_set_team"}
    try:
        assert apply_toolset("core") == "core"
        assert {t.name for t in await app.mcp.list_tools()} == full - {"wpilib_tool", "wpilib_vendordep", "wpilib_set_team"}
    finally:
        assert apply_toolset("full") == "full"
    assert {t.name for t in await app.mcp.list_tools()} == full
    with pytest.raises(VSCodeError, match="VSCODE_TOOLSET"):
        apply_toolset("everything")


async def test_annotations_are_honest(client):
    tools = {t.name: t.annotations for t in await client.list_tools()}
    assert tools["wpilib_info"].read_only_hint is True
    assert tools["wpilib_deploy"].destructive_hint is True and tools["wpilib_run"].read_only_hint is False

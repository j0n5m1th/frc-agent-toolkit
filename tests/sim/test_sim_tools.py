"""The simulation tools end to end: a real MCP client drives a fake Gradle project and sees the JSON a model would."""

import asyncio
import json
import os
import socket

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_sim.app import apply_toolset


async def call(client, name, args=None):
    result = await client.call_tool(name, args or {})
    return json.loads(result.content[0].text)


async def test_start_waits_for_startup_complete_and_reports_ready(client):
    out = await call(client, "sim_start", {"props": {"mode": "teleop", "headless": ""}, "wait_s": 30})
    assert out["state"] == "ready" and out["errors"] == 0 and out["warnings"] == 1 and out["ready_after_s"] >= 0
    lines = (await call(client, "sim_output", {"name_contains": "args="}))["lines"]
    assert lines[0].endswith("args=simulateJava --console=plain -Pmode=teleop -Pheadless")  # an empty value is a flag
    stopped = await call(client, "sim_stop")
    assert stopped["state"] == "stopped" and "exit_code" in stopped


async def test_a_build_failure_comes_back_with_the_error_lines(client):
    out = await call(client, "sim_start", {"props": {"scenario": "buildfail"}, "wait_s": 30})
    assert out["state"] == "exited" and out["exit_code"] == 1
    assert any("cannot find symbol" in line for line in out["tail"])


async def test_a_crash_after_starting_is_not_reported_as_ready(client):
    out = await call(client, "sim_start", {"props": {"scenario": "crash"}, "wait_s": 30})
    assert out["state"] == "exited" and out["exit_code"] == 2 and out["errors"] >= 2
    errors = (await call(client, "sim_output", {"level": "error"}))["lines"]
    assert any("NullPointerException" in line for line in errors) and any("Robot.java:42" in line for line in errors)


async def test_a_sim_that_never_reports_ready_is_still_building_or_starting(client):
    out = await call(client, "sim_start", {"props": {"scenario": "never_ready"}, "wait_s": 1})
    assert out["state"] == "starting" and "Not ready" in out["hint"]


async def test_repeated_lines_are_folded_and_since_returns_only_new_lines(client):
    await call(client, "sim_start", {"props": {"scenario": "spam"}, "wait_s": 30})
    first = await call(client, "sim_output", {"level": "warn"})
    overrun = [line for line in first["lines"] if "Loop time" in line]
    assert len(overrun) == 1 and overrun[0].endswith("(x30)")
    again = await call(client, "sim_output", {"since": first["next_offset"]})
    assert again["lines"] == [] and again["next_offset"] == first["next_offset"]


async def test_tail_keeps_the_newest_and_counts_the_rest(client):
    await call(client, "sim_start", {"wait_s": 30})
    out = await call(client, "sim_output", {"tail": 2})
    assert len(out["lines"]) == 2 and out["omitted"] >= 1


async def test_only_one_sim_runs_at_a_time(client):
    await call(client, "sim_start", {"wait_s": 30})
    with pytest.raises(ToolError, match="already running"):
        await call(client, "sim_start", {})


async def test_a_second_start_works_after_stop_and_replaces_the_console(client):
    await call(client, "sim_start", {"props": {"scenario": "crash"}, "wait_s": 30})
    again = await call(client, "sim_start", {"wait_s": 30})
    assert again["state"] == "ready" and again["errors"] == 0


async def test_stop_reports_the_run_it_stopped(client):
    started = await call(client, "sim_start", {"wait_s": 30})
    stopped = await call(client, "sim_stop")
    # one snapshot of the single slot: the reply is about the run that was just stopped, not a later one
    assert stopped["state"] == "stopped" and stopped["pid"] == started["pid"] and stopped["task"] == started["task"]


async def test_status_before_any_run_is_idle_and_output_says_to_start(client):
    idle = await call(client, "sim_status")
    # the idle reply reports the settings sim_start would use, not just that nothing has run
    assert idle["state"] == "idle" and idle["nt_port_open"] is False
    assert idle["default_task"] == "simulateJava" and idle["nt_port"] == 58997 and idle["max_run_s"] == 1800
    with pytest.raises(ToolError, match="sim_start"):
        await call(client, "sim_output")
    assert (await call(client, "sim_stop"))["hint"].startswith("No sim has run")


@pytest.mark.slow
async def test_the_run_stops_itself_after_run_s(client):
    await call(client, "sim_start", {"wait_s": 30, "run_s": 1})
    await asyncio.sleep(2)
    status = await call(client, "sim_status")
    assert status["state"] == "timed_out"


async def test_a_busy_port_refuses_the_start(client, monkeypatch):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        monkeypatch.setenv("SIM_NT_PORT", str(server.getsockname()[1]))
        with pytest.raises(ToolError, match="already in use"):
            await call(client, "sim_start", {})
        assert (await call(client, "sim_status"))["nt_port_open"] is True


async def test_the_tool_surface_is_four_tools_with_honest_annotations(client):
    tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {"sim_start", "sim_stop", "sim_status", "sim_output"}
    assert tools["sim_status"].annotations.read_only_hint and tools["sim_output"].annotations.read_only_hint
    assert not tools["sim_start"].annotations.read_only_hint and not tools["sim_start"].annotations.destructive_hint


async def test_sim_toolset_core_keeps_the_three_always_on_tools(client, monkeypatch):
    """The same core/full switch the other servers have: the console dump is the one tool worth hiding."""
    monkeypatch.setenv("SIM_TOOLSET", "core")
    assert apply_toolset() == "core"
    assert {t.name for t in await client.list_tools()} == {"sim_start", "sim_stop", "sim_status"}
    assert apply_toolset("full") == "full"  # the rest of the file sees the whole surface again


async def test_an_unknown_sim_toolset_is_an_error_naming_the_variable(monkeypatch):
    monkeypatch.setenv("SIM_TOOLSET", "everything")
    with pytest.raises(Exception, match="SIM_TOOLSET"):
        apply_toolset()


async def test_the_sim_runs_with_an_explicit_jdk_and_status_says_which(client, tmp_path, monkeypatch):
    jdk = tmp_path / "jdk"
    (jdk / "bin").mkdir(parents=True)
    (jdk / "bin" / ("java.exe" if os.name == "nt" else "java")).write_text("", encoding="utf-8")
    monkeypatch.setenv("WPILIB_JAVA_HOME", str(jdk))
    out = await call(client, "sim_start", {"wait_s": 30})
    assert out["java"] == str(jdk)
    assert (await call(client, "sim_output", {"name_contains": "JAVA_HOME="}))["lines"][0].endswith(f"JAVA_HOME={jdk}")


async def test_status_names_the_wpilog_the_run_writes(client):
    out = await call(client, "sim_start", {"wait_s": 30})
    assert out["wpilog"].endswith("WPILIB_2026.wpilog") and "TBD" not in out["wpilog"]  # the rename line is followed


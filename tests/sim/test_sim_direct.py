"""The simulation tools and the run object driven directly, without an MCP client."""

import asyncio
import json

import pytest

from frc_mcp_sim.run import SimRun
from frc_mcp_sim.tools.console_tools import sim_output, sim_status
from frc_mcp_sim.tools.run_tools import sim_start, sim_stop


def test_a_run_knows_its_own_phase_from_the_lines_it_prints(project):
    run = SimRun(project, "simulateJava", ["-Pmode=teleop"])
    assert run.task == "simulateJava" and run.prop_args == ["-Pmode=teleop"]
    assert run.args == ["simulateJava", "--console=plain", "-Pmode=teleop"]
    assert run.phase == "building" and run.ready_at is None and run.active is True
    run._on_line("********** Robot program starting **********")
    assert run.phase == "starting" and run.ready_at is None
    run._on_line("********** Robot program startup complete **********")
    assert run.phase == "ready" and run.ready_at is not None


async def test_the_four_tools_answer_when_called_directly(project):
    started = json.loads((await sim_start({"mode": "teleop"}, None, 30, None, None)).content[0].text)
    assert started["state"] == "ready" and started["task"] == "simulateJava" and started["nt_port_open"] is False
    assert started["prop_args"] == ["-Pmode=teleop"] and started["ready_after_s"] >= 0

    status = json.loads((await sim_status()).content[0].text)
    assert status["state"] == "ready" and status["errors"] == 0 and status["lines"] > 0
    # the settings in force come with the status, as they do on the idle reply below (58997 from the `project` fixture)
    assert status["default_task"] == "simulateJava" and status["nt_port"] == 58997 and status["max_run_s"] == 1800

    shown = json.loads((await sim_output(since=None, tail=2, level="warn", name_contains="Loop")).content[0].text)
    assert shown["state"] == "ready" and shown["next_offset"] == status["lines"] and len(shown["lines"]) <= 2
    everything = json.loads((await sim_output(tail=300)).content[0].text)
    assert any("startup complete" in line for line in everything["lines"])

    stopped = json.loads((await sim_stop()).content[0].text)
    assert stopped["state"] == "stopped" and "exit_code" in stopped and "hint" not in stopped
    assert stopped["exit_code"] == 0 or "exit_expected" in stopped  # a killed run exits non-zero, and the reply says that is normal


@pytest.mark.slow
async def test_a_start_that_is_still_building_carries_the_last_lines(project):
    started = json.loads((await sim_start({"scenario": "never_ready"}, None, 1, None, None)).content[0].text)
    assert started["state"] == "starting" and "Not ready after 1 s" in started["hint"]
    assert started["tail"] and any("Robot program starting" in line for line in started["tail"])
    await sim_stop()


async def test_stop_before_any_run_says_so(project):
    out = json.loads((await sim_stop()).content[0].text)
    assert out["state"] == "idle" and out["hint"].startswith("No sim has run")


async def test_wait_port_closed_waits_out_a_port_that_closes_late():
    from frc_mcp_sim.session import wait_port_closed

    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    asyncio.get_running_loop().call_later(0.6, server.close)  # the dying sim lets go a moment after the kill
    assert await wait_port_closed(port, timeout_s=5) is True


async def test_wait_port_closed_gives_up_on_a_port_that_stays_open():
    from frc_mcp_sim.session import wait_port_closed

    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    try:
        assert await wait_port_closed(server.sockets[0].getsockname()[1], timeout_s=0.6) is False
    finally:
        server.close()


def test_error_lines_after_a_stop_are_not_counted_as_the_sims_errors(project):
    run = SimRun(project, "simulateJava", [])
    run._on_line("ERROR: a real fault while running")
    run.console.ending = True  # what stop() and the time limit set before they kill the process
    run._on_line("FAILURE: Build failed with an exception.")
    assert run.console.errors == 1 and run.console.errors_at_end == 1
    run.end_reason, run.exit_code = "stopped", 1
    from frc_mcp_sim.session import session

    session.run = run
    out = asyncio.run(session.status())
    assert out["errors"] == 1 and out["errors_at_stop"] == 1 and "exit_expected" in out
    session.run = None

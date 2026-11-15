"""Restarting robot code and rebooting: the switch, the enabled / field guard, and what each command does."""

import contextlib
import time

import pytest
from envelope_helpers import j
from fastmcp.exceptions import ToolError
from systemcore_helpers import FakeRobotProgram, FakeSession

from frc_mcp_systemcore import restart, robot_state, ssh
from frc_mcp_systemcore.errors import SystemcoreError


async def call(client, name, args=None):
    return j(await client.call_tool(name, args or {}))


class Controller(FakeSession):
    """A fake controller shell: the robot service gets a new process on restart, and a reboot resets the uptime the HTTP service reports."""

    def __init__(self, device=None, sudo_ok=True):
        super().__init__({})
        self.pid, self.device, self.sudo_ok = 100, device, sudo_ok
        self.state = "active"

    def run(self, command, timeout=30):
        self.ran.append(command)
        if command == restart.SHOW:
            return 0, f"ActiveState={self.state}\nSubState=running\nMainPID={self.pid}\nNRestarts=0\n", ""
        if command in (restart.RESTART, restart.REBOOT):
            if not self.sudo_ok:
                return 1, "", "sudo: a password is required"
            if command == restart.RESTART:
                self.pid, self.state = self.pid + 1, "active"
            elif self.device is not None:
                self.device.routes[("GET", "/api/time")] = {"epoch_ms": 0, "ntp_synced": False, "timezone": "CDT", "uptime_sec": 1.5}
        return 0, "", ""


@pytest.fixture
def allowed(monkeypatch, fast_time):
    monkeypatch.setenv("SYSTEMCORE_ALLOW_RESTART", "1")
    fast_time(restart)  # the waits for the robot to come back run on a fake clock
    monkeypatch.setattr(restart, "SETTLE_S", 0.0)


@pytest.fixture
def robot(monkeypatch):
    """What the robot program reports: set `.word` to a dict, or None when it is down."""

    class State:
        word = {"enabled": False, "eStop": False, "fmsAttached": False, "dsAttached": True}

    async def read(host, wait_s=3.0):
        return State.word

    monkeypatch.setattr(robot_state, "control_word", read)
    return State


@pytest.fixture
def shell(monkeypatch, device):
    controller = Controller(device)

    @contextlib.asynccontextmanager
    async def opened(host, os_hash=None):
        yield controller

    monkeypatch.setattr(ssh, "session", opened)
    return controller


# ---- the switch and the guard ----

async def test_both_tools_are_off_until_the_owner_allows_them(client, shell, robot):
    for tool in ("systemcore_restart_robot_code", "systemcore_reboot"):
        with pytest.raises(ToolError, match="SYSTEMCORE_ALLOW_RESTART=1"):
            await client.call_tool(tool, {})
    assert shell.ran == []


@pytest.mark.parametrize("flags", [{"enabled": True}, {"fmsAttached": True}, {"enabled": True, "fmsAttached": True}])
async def test_an_enabled_robot_or_one_on_a_field_is_never_restarted_or_rebooted(client, shell, robot, allowed, flags):
    robot.word = {**robot.word, **flags}
    for tool in ("systemcore_restart_robot_code", "systemcore_reboot"):
        with pytest.raises(ToolError, match="Disable the robot first"):
            await client.call_tool(tool, {})
    assert shell.ran == []  # nothing reached the controller


@pytest.mark.parametrize("tool", ["systemcore_restart_robot_code", "systemcore_reboot"])
async def test_an_unreadable_state_refuses_while_the_robot_service_runs(client, shell, robot, allowed, tool):
    robot.word = None
    with pytest.raises(ToolError, match="force=True"):
        await client.call_tool(tool, {})
    assert restart.RESTART not in shell.ran and restart.REBOOT not in shell.ran


async def test_an_unreadable_state_with_the_service_stopped_goes_ahead(client, shell, robot, allowed):
    robot.word = None
    for state in ("inactive", "failed"):
        shell.state = state
        out = await call(client, "systemcore_restart_robot_code")
        assert out["restarted"] is True and "unknown" in out["robot_state"]
    shell.state = "inactive"
    assert (await call(client, "systemcore_reboot", {"wait_s": 20}))["rebooting"] is True


@pytest.mark.parametrize("tool", ["systemcore_restart_robot_code", "systemcore_reboot"])
async def test_force_covers_an_unreadable_state(client, shell, robot, allowed, tool):
    robot.word = None
    out = await call(client, tool, {"force": True})
    assert "forced" in out["robot_state"]


async def test_force_never_overrides_enabled_or_a_field(client, shell, robot, allowed):
    robot.word = {**robot.word, "enabled": True}
    for tool in ("systemcore_restart_robot_code", "systemcore_reboot"):
        with pytest.raises(ToolError, match="Disable the robot first"):
            await client.call_tool(tool, {"force": True})
    assert shell.ran == []


# ---- restart robot code ----

async def test_restart_robot_code_waits_for_a_new_process(client, shell, robot, allowed):
    out = await call(client, "systemcore_restart_robot_code")
    assert out["restarted"] is True and out["robot_service"]["state"] == "active"
    assert out["robot_state"] == {"enabled": False, "eStop": False, "dsAttached": True}
    assert shell.ran.count(restart.RESTART) == 1 and shell.pid == 101


async def test_restart_reports_a_service_that_did_not_come_back(client, shell, robot, allowed):
    original = shell.run

    def stuck(command, timeout=30):  # the restart is accepted but the main process never changes
        out = original(command, timeout)
        if command == restart.RESTART:
            shell.pid -= 1
        return out

    shell.run = stuck
    out = await call(client, "systemcore_restart_robot_code", {"wait_s": 5})
    assert out["restarted"] is False and "systemcore_journal" in out["hint"]


async def test_a_sudo_that_wants_a_password_is_reported_plainly(client, shell, robot, allowed):
    shell.sudo_ok = False
    with pytest.raises(ToolError, match="sudo needs a password"):
        await client.call_tool("systemcore_restart_robot_code", {})


# ---- reboot ----

async def test_reboot_waits_for_the_controller_to_answer_with_a_smaller_uptime(client, shell, robot, allowed, device):
    out = await call(client, "systemcore_reboot", {"wait_s": 20})
    assert out["rebooting"] is True and out["came_back"] is True and shell.ran.count(restart.REBOOT) == 1


async def test_reboot_says_what_to_do_when_the_controller_does_not_come_back(client, shell, robot, allowed, device):
    shell.device = None  # the uptime never resets
    out = await call(client, "systemcore_reboot", {"wait_s": 10})
    assert out["came_back"] is False and "systemcore_connect" in out["hint"]


def test_the_commands_never_ask_for_a_password_and_the_reboot_starts_after_a_pause():
    assert restart.REBOOT.startswith("sudo -n ") and restart.RESTART.startswith("sudo -n ")
    assert "sleep 2" in restart.REBOOT and restart.REBOOT.rstrip("'").endswith("&")  # in the background: the command returns first


def test_a_command_that_sudo_refuses_names_the_reason():
    assert "sudo needs a password" in str(restart._sudo_failed(1, "sudo: a password is required"))
    assert "exit 7" in str(restart._sudo_failed(7, ""))
    assert isinstance(restart._sudo_failed(1, "x"), SystemcoreError)


# ---- the guard's read of the real NetworkTables ControlWord ----

def test_the_control_word_is_read_from_a_real_networktables_server(monkeypatch):
    fake = FakeRobotProgram()
    try:
        monkeypatch.setenv("SYSTEMCORE_PORTS", f"nt={fake.start(enabled=True, ds=True)}")
        word = robot_state.control_word_sync("127.0.0.1", 5)
        assert word is not None and word["enabled"] is True and word["dsAttached"] is True and word["fmsAttached"] is False
        assert robot_state.blockers(word) == ["enabled"]
        fake.set(fms=True)
        time.sleep(0.2)
        assert robot_state.blockers(robot_state.control_word_sync("127.0.0.1", 5)) == ["fmsAttached"]
    finally:
        fake.stop()


def test_a_missing_robot_program_is_unknown_and_blocks_nothing(monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_PORTS", "nt=9")  # nothing listens here
    assert robot_state.control_word_sync("127.0.0.1", 0.5) is None
    assert robot_state.blockers(None) == [] and robot_state.blockers({"enabled": False, "fmsAttached": False}) == []

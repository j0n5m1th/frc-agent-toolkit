"""frc_mcp_core.processes: killing a whole process tree, the Windows kill-on-close job, and the CTRE runner's clean-up."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from frc_mcp_core import processes
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.runner import run_tool

SLEEP = "import time; time.sleep(60)"
# a parent that starts a grandchild, prints the grandchild's pid, then sleeps
PARENT = ("import subprocess, sys, time\n"
          f"c = subprocess.Popen([sys.executable, '-c', {SLEEP!r}])\n"
          "print(c.pid, flush=True)\n"
          "time.sleep(60)\n")


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def gone(*pids: int, timeout_s: float = 10) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not any(alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


def start_parent(code: str = PARENT) -> tuple[subprocess.Popen[str], int]:
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, **processes.new_session_kwargs())
    assert proc.stdout is not None
    return proc, int(proc.stdout.readline())


def test_kill_tree_ends_the_child_and_its_own_child():
    proc, grandchild = start_parent()
    try:
        processes.kill_tree_sync(proc.pid)
        proc.wait(10)  # reap it so a POSIX zombie does not look alive
        assert gone(proc.pid, grandchild)
    finally:
        processes.kill_tree_sync(proc.pid)
        if proc.stdout:
            proc.stdout.close()
        if alive(grandchild):  # only if the assert above failed: do not leave it running
            processes.kill_tree_sync(grandchild)


def test_kill_tree_on_a_process_that_is_gone_is_quiet():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(10)
    processes.kill_tree_sync(proc.pid)


def test_a_taskkill_that_times_out_is_caught(monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(processes.subprocess, "run", slow)
    processes.kill_tree_sync(123456789)  # must not raise
    monkeypatch.setattr(processes.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("taskkill")))
    processes.kill_tree_sync(123456789)


def test_new_session_kwargs_are_only_for_posix():
    assert processes.new_session_kwargs() == ({} if sys.platform == "win32" else {"start_new_session": True})


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows only")
def test_a_tracked_child_is_assigned_to_the_job_and_dies_with_the_server():
    code = ("import subprocess, sys, time\n"
            "from frc_mcp_core.processes import track_child\n"
            f"c = subprocess.Popen([sys.executable, '-c', {SLEEP!r}])\n"
            "print(c.pid, track_child(c.pid), flush=True)\n"
            "time.sleep(60)\n")
    server = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert server.stdout is not None
    pid, assigned = server.stdout.readline().split()
    try:
        assert assigned == "True" and alive(int(pid))
        server.kill()  # a hard kill: no atexit, no cleanup of its own
        server.wait(10)
        assert gone(int(pid))  # the job closed with the server, so Windows ended the child
    finally:
        server.kill()
        server.stdout.close()
        if alive(int(pid)):
            processes.kill_tree_sync(int(pid))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no process groups")
def test_a_posix_child_started_with_a_new_session_is_killed_as_a_group():
    proc, grandchild = start_parent()
    try:
        assert os.getpgid(proc.pid) == proc.pid
        processes.kill_tree_sync(proc.pid)
        proc.wait(10)
        assert gone(grandchild)
    finally:
        processes.kill_tree_sync(proc.pid)
        proc.stdout.close()  # type: ignore[union-attr]


def pid_script(pid_file: Path) -> str:
    return f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"


async def wait_for_pid(pid_file: Path) -> int:
    for _ in range(200):
        if pid_file.exists() and pid_file.read_text():
            return int(pid_file.read_text())
        await asyncio.sleep(0.05)
    raise AssertionError("the tool never started")


async def test_cancelling_a_ctre_tool_call_kills_the_child(tmp_path):
    pid_file = tmp_path / "pid"
    task = asyncio.create_task(run_tool(Path(sys.executable), ["-c", pid_script(pid_file)], 60))
    pid = await wait_for_pid(pid_file)
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gone(pid)
    finally:
        if alive(pid):
            processes.kill_tree_sync(pid)


async def test_a_ctre_tool_that_times_out_is_killed_with_its_children(tmp_path):
    pid_file = tmp_path / "pid"
    with pytest.raises(CtreError, match="did not finish within 1 s"):
        await run_tool(Path(sys.executable), ["-c", pid_script(pid_file)], 1)
    pid = int(pid_file.read_text())
    try:
        assert gone(pid)
    finally:
        if alive(pid):
            processes.kill_tree_sync(pid)


def fake_signal_module(monkeypatch, current=None):
    """Record what `exit_on_signals` installs without touching the real process's handlers."""
    import signal

    installed: dict[int, object] = {}
    monkeypatch.setattr(signal, "getsignal", lambda signum: signal.SIG_DFL if current is None else current)
    monkeypatch.setattr(signal, "signal", lambda signum, handler: installed.__setitem__(signum, handler))
    return installed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_the_termination_signals_get_a_handler_that_cleans_up_and_exits(monkeypatch):
    import signal

    installed = fake_signal_module(monkeypatch)
    taken = processes.exit_on_signals()
    assert taken == [int(signal.SIGTERM), int(signal.SIGHUP)]
    ran: list[str] = []
    exits: list[int] = []
    monkeypatch.setattr(processes, "run_cleanups", lambda: ran.append("cleanup"))
    monkeypatch.setattr(processes.os, "_exit", exits.append)
    installed[taken[0]](taken[0], None)  # type: ignore[operator]
    assert ran == ["cleanup"] and exits == [128 + taken[0]]
    assert all(installed[n] is signal.SIG_IGN for n in taken)  # a second signal during the clean-up does nothing


def test_the_handler_still_exits_when_a_clean_up_fails(monkeypatch):
    fake_signal_module(monkeypatch)
    exits: list[int] = []
    monkeypatch.setattr(processes.os, "_exit", exits.append)
    monkeypatch.setattr(processes, "_cleanups", [lambda: 1 / 0, lambda: exits.append(-1)])
    processes._exit_on_signal(15, None)
    assert exits == [-1, 143]  # the failing clean-up did not stop the next one or the exit


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_signal_that_already_has_a_handler_is_left_alone(monkeypatch):
    installed = fake_signal_module(monkeypatch, current=print)
    assert processes.exit_on_signals() == [] and installed == {}


def test_remembered_children_are_killed_by_the_clean_up_and_forgotten_ones_are_not(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(processes, "kill_tree_sync", killed.append)
    monkeypatch.setattr(processes, "_children", set())
    processes.remember_child(11)
    processes.remember_child(12)
    processes.forget_child(12)
    processes.run_cleanups()
    assert killed == [11]


# A server stand-in: takes over the signals, starts a tracked child, keeps stdin open with a non-daemon reader thread (as MCP's stdio does).
SERVER_STAND_IN = """
import subprocess, sys, threading, time
from frc_mcp_core import processes
processes.exit_on_signals()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], **processes.new_session_kwargs())
processes.track_child(child.pid)
processes.remember_child(child.pid)
threading.Thread(target=sys.stdin.readline).start()  # non-daemon: Python waits for it before atexit
print(child.pid, flush=True)
time.sleep(120)
"""


def test_a_termination_signal_kills_the_children_and_stops_the_server_even_with_stdin_open():
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {}  # type: ignore[attr-defined]
    server = subprocess.Popen([sys.executable, "-c", SERVER_STAND_IN], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, **flags)
    assert server.stdout is not None and server.stdin is not None
    child = int(server.stdout.readline())
    try:
        assert alive(child)
        if sys.platform == "win32":
            import signal

            server.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]  # the one signal a console can send there; the job ends the child
        else:
            server.terminate()  # SIGTERM, with stdin still open
        code = server.wait(15)
        assert code != 0 and gone(child, timeout_s=10)
    finally:
        server.kill()
        server.stdin.close()
        server.stdout.close()
        server.wait(10)
        if alive(child):
            processes.kill_tree_sync(child)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_second_signal_during_the_clean_up_does_not_stop_it():
    code = SERVER_STAND_IN.replace("print(child.pid, flush=True)", "print(child.pid, flush=True)\nprocesses.register_cleanup(lambda: time.sleep(1))")
    server = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert server.stdout is not None and server.stdin is not None
    child = int(server.stdout.readline())
    try:
        import signal

        server.send_signal(signal.SIGTERM)
        time.sleep(0.3)
        server.send_signal(signal.SIGHUP)  # a closing terminal follows SIGTERM with SIGHUP
        server.wait(15)
        assert gone(child, timeout_s=10)
    finally:
        server.kill()
        server.stdin.close()
        server.stdout.close()
        server.wait(10)
        if alive(child):
            processes.kill_tree_sync(child)


def test_signals_are_left_alone_on_windows(monkeypatch):
    monkeypatch.setattr(processes.sys, "platform", "win32")
    installed = fake_signal_module(monkeypatch)
    assert processes.exit_on_signals() == [] and installed == {}

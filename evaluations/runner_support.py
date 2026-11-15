"""Setup shared by the evaluation runner: a fresh fake robot per question, the MCP server environment and config.

Kept apart from run_with_claude_code.py so the client runners and CLI handling there stay focused.
"""

import contextlib
import json
import subprocess  # nosec B404 - only named in type annotations; the process itself is started by eval_common
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import fake_log
import ntcore
from eval_common import LOG_SERVER, ROOT, SERVER, atomic_write_text, fake_robot_process

FAKE_WRITE_PREFIXES = "/Fake/Tunable/,/Fake/Notes/,/Fake/Auto Chooser/"
READINESS_TIMEOUT_S = 10
READINESS_POLL_S = 0.05


@contextlib.contextmanager
def fake_robot(port: int, phase: float, *, readiness_timeout: float = READINESS_TIMEOUT_S) -> Iterator[None]:
    """A fresh deterministic fake robot (its signals restart, earlier writes are gone)."""
    with tempfile.TemporaryDirectory(prefix="fake-robot-eval-") as tmp:
        run_id = uuid.uuid4().hex
        extra = ["--phase-seconds", str(phase), "--run-id", run_id,
                 "--persistence-file", str(Path(tmp) / "networktables.json")]
        with fake_robot_process(port, extra) as proc:
            inst = ntcore.NetworkTableInstance.create()
            try:
                _wait_until_ready(inst, proc, port, run_id, readiness_timeout)
            finally:
                ntcore.NetworkTableInstance.destroy(inst)
            yield


def _wait_until_ready(inst: ntcore.NetworkTableInstance, proc: subprocess.Popen, port: int, run_id: str,
                      readiness_timeout: float) -> None:
    """Block until the robot we just started (identified by its run id marker) is visible, else raise."""
    inst.startClient4("evaluation-readiness")
    inst.setServer("127.0.0.1", port)
    marker = inst.getStringTopic("/Fake/Evaluation/RunId").subscribe("")
    deadline = time.monotonic() + readiness_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"fake robot exited before becoming ready on port {port}")
        if inst.isConnected() and marker.get() == run_id:
            return
        time.sleep(READINESS_POLL_S)
    raise RuntimeError(f"fake robot did not become ready on port {port}; check whether the port is in use")


def safety_environment(prefixes: str | None) -> dict[str, str]:
    if prefixes:
        return {"NT4_ALLOW_WRITES": "1", "NT4_READ_ONLY": "0", "NT4_WRITE_PREFIXES": prefixes}
    return {"NT4_ALLOW_WRITES": "0", "NT4_READ_ONLY": "1", "NT4_WRITE_PREFIXES": ""}


def server_environment(host: str, port: int, prefixes: str | None) -> dict[str, str]:
    """Environment for the NT4 MCP server: where to connect and which writes are allowed."""
    return {"NT4_HOST": host, "NT4_PORT": str(port), **safety_environment(prefixes)}


def mcp_config(env: dict[str, str], server: str = SERVER) -> dict[str, object]:
    """MCP config (for `claude --mcp-config`) that launches the server with this key (`frc-mcp <server>`) with `env`."""
    return {"mcpServers": {server: {
        "command": "uv",
        "args": ["run", "--project", str(ROOT / "plugins" / "frc"), "frc-mcp", server],
        "env": env,
    }}}


def write_mcp_config(path: Path, env: dict[str, str], server: str = SERVER) -> None:
    atomic_write_text(path, json.dumps(mcp_config(env, server)))


@contextlib.contextmanager
def practice_log() -> Iterator[dict[str, str]]:
    """A folder holding the deterministic practice log; yields the log server's environment pointing at it."""
    with tempfile.TemporaryDirectory(prefix="fake-log-eval-") as tmp:
        fake_log.build().save(Path(tmp) / "practice.wpilog")
        yield {"WPILOG_DIR": tmp}


def log_server_config(path: Path, env: dict[str, str]) -> None:
    """Write the MCP config that launches the log server (its tools are named mcp__wpilog__*)."""
    write_mcp_config(path, env, LOG_SERVER)

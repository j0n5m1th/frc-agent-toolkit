"""Fixtures for the VS Code command server tests: a fake robot project and a fake WPILib install with offline vendordeps."""

import json

import pytest
from fastmcp import Client
from vscode_helpers import fake_java, make_project, vendordep

from frc_mcp_vscode import runner
from frc_mcp_vscode.spec import SPEC

mcp, _ = SPEC.load()


@pytest.fixture(autouse=True)
async def clean_runner():
    """A GUI tool a test started is stopped when it ends."""
    runner.tools.clear()
    yield
    for run in list(runner.tools.values()):
        await run.stop()
    runner.tools.clear()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A fake robot project, the only runnable folder, with a fake WPILib install (CommandsV2 1.0.1, CommandsV3, AprilTag)."""
    home = tmp_path / "wpilib"
    offline = home / "2027_alpha7" / "vendordeps"
    offline.mkdir(parents=True)
    for dep in (vendordep("CommandsV2", "1.0.1", "u-v2", ["u-v3"]), vendordep("CommandsV3", "1.0.0", "u-v3", ["u-v2"]),
                vendordep("AprilTag", "2.0.0", "u-tag")):
        (offline / f"{dep['name']}.json").write_text(json.dumps(dep), encoding="utf-8")
    fake_java(home / "2027_alpha7" / "jdk")  # what the installer puts next to the project's year
    monkeypatch.setenv("WPILIB_HOME", str(home))
    monkeypatch.delenv("WPILIB_JAVA_HOME", raising=False)
    monkeypatch.setenv("VSCODE_PROJECT_DIR", str(tmp_path / "work"))
    monkeypatch.delenv("VSCODE_ALLOW_DEPLOY", raising=False)
    return make_project(tmp_path / "work" / "robot")


@pytest.fixture
async def client(project):
    async with Client(mcp) as c:
        yield c

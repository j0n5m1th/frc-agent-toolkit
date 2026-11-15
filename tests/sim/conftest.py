"""Fixtures for the simulation server tests: a fake WPILib project whose Gradle wrapper is a small Python script."""

import pytest
from fastmcp import Client
from sim_helpers import make_project

from frc_mcp_sim.session import session
from frc_mcp_sim.spec import SPEC

mcp, _ = SPEC.load()


@pytest.fixture(autouse=True)
async def clean_session():
    """Each test starts with no run, and any sim it left behind is stopped."""
    session.run = None
    yield
    if session.run:
        await session.run.stop()
    session.run = None


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A fake robot project, set as the only runnable folder, on a NetworkTables port nothing listens on."""
    monkeypatch.setenv("SIM_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("SIM_NT_PORT", "58997")
    monkeypatch.delenv("WPILIB_JAVA_HOME", raising=False)
    return make_project(tmp_path / "robot")


@pytest.fixture
async def client(project):
    async with Client(mcp) as c:
        yield c

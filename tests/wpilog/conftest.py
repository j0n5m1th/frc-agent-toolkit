"""Fixtures for the log server tests: a small sample log in a temporary folder the server is allowed to read."""

import pytest
from fastmcp import Client
from log_builder import build_sample_log

from frc_mcp_wpilib.schema import state as schema_state
from frc_mcp_wpilog import store
from frc_mcp_wpilog.spec import SPEC

mcp, _ = SPEC.load()


@pytest.fixture(autouse=True)
def clean_server_state():
    """Each test starts with no open logs and no schemas loaded."""
    store.logs.reset()
    schema_state.forget()
    yield
    store.logs._logs.clear()
    schema_state.forget()


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """A folder holding `sample.wpilog`, set as the only readable folder."""
    build_sample_log(tmp_path / "sample.wpilog")
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
async def client(log_dir):
    """An MCP client connected to the log server."""
    async with Client(mcp) as c:
        yield c


@pytest.fixture
def sample(log_dir):
    """The sample log, opened."""
    from frc_mcp_wpilog.logfile import LogFile

    return LogFile(log_dir / "sample.wpilog")

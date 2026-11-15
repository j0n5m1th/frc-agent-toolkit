"""Fixtures for the server tests: a real ntcore server on PORT and a FastMCP client connected to it.

The two isolation fixtures are opt-in (see `pytestmark` in the test_server_*.py files) so other test files keep a clean env.

Layout rule for this folder: the root holds `conftest.py`, the helper modules every cluster shares
(`nt_helpers.py`, `capture_helpers.py`) and the one-to-one tests of `frc_mcp_nt4`'s own root modules; each
source subpackage has a folder of the same name (`access/`, `capture/`, `tools/`, `session/`, `integration/`,
`evaluations/`).
"""

import ntcore
import pytest
from fastmcp import Client
from nt_helpers import PORT

from frc_mcp_nt4.session import session
from frc_mcp_nt4.spec import SPEC

mcp, _ = SPEC.load()


@pytest.fixture
def isolate_capture_logs(tmp_path, monkeypatch):
    """Tests must never create or rotate a user's actual capture logs."""
    monkeypatch.setenv("NT4_LOG_DIR", str(tmp_path / "capture-logs"))


@pytest.fixture
def allow_test_writes(monkeypatch):
    monkeypatch.setenv("NT4_ALLOW_WRITES", "1")
    monkeypatch.setenv("NT4_WRITE_PREFIXES", "/t,/ro,/Test,/idle,/i,/pp,/pq,/d,/ro2,/im,/gh,/ec,/rb,/bad,/ok")
    monkeypatch.delenv("NT4_READ_ONLY", raising=False)


@pytest.fixture
def nt_server(tmp_path):
    srv = ntcore.NetworkTableInstance.create()
    # persistent topics make ntcore save a file; an empty name would write ./.tmp into the repo
    srv.startServer(persist_filename=str(tmp_path / "nt.json"), listen_address="127.0.0.1", port3=PORT + 1, port4=PORT)
    yield srv
    session.disconnect()
    ntcore.NetworkTableInstance.destroy(srv)


@pytest.fixture
async def client(nt_server):
    async with Client(mcp) as c:
        # startServer returns before the listener is necessarily accepting, and a full run under load has missed the
        # default 5 s; a server that never comes up still fails, just after this longer wait
        await c.call_tool("nt4_connection", {"host": "127.0.0.1", "port": PORT, "wait_s": 15})
        yield c

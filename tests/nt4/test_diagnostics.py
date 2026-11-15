import subprocess
import sys

import pytest
from nt_helpers import NT4_MAIN

from frc_mcp_nt4.diagnostics import diagnose


@pytest.fixture(autouse=True)
def clear_nt_env(monkeypatch):
    for key in ("NT4_HOST", "NT4_PORT", "NT4_IDLE_SECONDS", "NT4_ALLOW_WRITES", "NT4_READ_ONLY", "NT4_WRITE_PREFIXES"):
        monkeypatch.delenv(key, raising=False)


async def test_diagnostic_requires_an_explicit_or_configured_host(capsys):
    assert await diagnose(None, None, 1) == 1
    assert "No robot address configured" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("NT4_PORT", "oops", "NT4_PORT"),
        ("NT4_PORT", "70000", "NT4_PORT must be a number from 1 to 65535"),
        ("NT4_IDLE_SECONDS", "oops", "NT4_IDLE_SECONDS"),
        ("NT4_IDLE_SECONDS", "nan", "NT4_IDLE_SECONDS must be a number from 0 to 86400"),
        ("NT4_IDLE_SECONDS", "-1", "NT4_IDLE_SECONDS must be a number from 0 to 86400"),
        ("NT4_ALLOW_WRITES", "maybe", "Invalid write safety configuration"),
        ("NT4_WRITE_PREFIXES", "/", "Invalid write safety configuration"),
    ],
)
async def test_diagnostic_reports_invalid_configuration_before_connecting(monkeypatch, capsys, name, value, message):
    monkeypatch.setenv(name, value)
    assert await diagnose("127.0.0.1", None, 1) == 1
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("port", [0, -1, 65536])
async def test_diagnostic_rejects_an_out_of_range_explicit_port(capsys, port):
    assert await diagnose("127.0.0.1", port, 1) == 1
    assert "Port must be between 1 and 65535." in capsys.readouterr().err


async def test_diagnostic_reports_connection_failure(capsys):
    assert await diagnose("127.0.0.1", 1, 0.1) == 1
    assert "No NT4 server answered" in capsys.readouterr().err


async def test_diagnostic_handles_connection_dropping_after_connect(monkeypatch, capsys):
    class DroppedSession:
        closed = False

        async def connect(self, host, port, identity, timeout_s):
            pass

        def status(self):
            return {"connected": False}

        async def disconnect_safely(self, wait=False):
            self.closed = True

    session = DroppedSession()
    monkeypatch.setattr("frc_mcp_nt4.diagnostics.session", session)  # the one module-level session, reused
    assert await diagnose("127.0.0.1", 5810, 1) == 1
    assert "Connection dropped" in capsys.readouterr().err
    assert session.closed


@pytest.mark.parametrize(
    "args",
    [
        ["check", "--timeout", "nan"],
        ["check", "--timeout", "0"],
        ["check", "--timeout", "61"],
        ["--host", "127.0.0.1"],
    ],
)
def test_cli_rejects_bad_options(args):
    result = subprocess.run(
        [sys.executable, "-c", NT4_MAIN, *args],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "Traceback" not in result.stderr

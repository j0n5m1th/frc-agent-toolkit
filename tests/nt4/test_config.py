"""Where capture logs go: NT4_LOG_DIR, else `logs/` in the server's project folder."""

from pathlib import Path

import pytest

from frc_mcp_nt4 import config
from frc_mcp_nt4.capture.log import new_log_path
from frc_mcp_nt4.errors import NT4Error


def test_default_log_dir_is_logs_in_the_server_project_folder(monkeypatch):
    monkeypatch.delenv("NT4_LOG_DIR", raising=False)
    folder = config.log_dir()
    assert folder.name == "logs" and folder.is_absolute()
    assert folder.parent == Path(__file__).resolve().parents[2]  # this checkout (the folder above `tests/`), not the home folder
    assert (folder.parent / "plugins" / "frc" / "pyproject.toml").is_file()


def test_nt4_log_dir_overrides_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("NT4_LOG_DIR", str(tmp_path / "mine"))
    assert config.log_dir() == tmp_path / "mine"
    path = new_log_path("c1")
    assert path.parent == tmp_path / "mine" and path.parent.is_dir()
    assert path.name.startswith("nt4-capture-") and path.suffix == ".wpilog"


def test_log_limit_error_names_the_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("NT4_LOG_DIR", str(tmp_path))
    for i in range(20):
        (tmp_path / f"nt4-capture-{i}.wpilog").write_bytes(b"")
    with pytest.raises(NT4Error, match="limit"):
        new_log_path("c9")


def test_the_controllers_port_is_never_writable_and_the_reason_names_no_setting(monkeypatch):
    monkeypatch.setenv("NT4_ALLOW_WRITES", "1")
    monkeypatch.setenv("NT4_WRITE_PREFIXES", "/Netcomm")
    assert config.write_restriction(port=5810) is None
    assert "controller's own server" in config.write_restriction(port=6810)
    with pytest.raises(NT4Error, match="read-only here"):
        config.check_writable("/Netcomm/Alerts/x", 6810)
    config.check_writable("/Netcomm/x", 5810)

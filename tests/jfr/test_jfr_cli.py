"""Command line (`frc_mcp_jfr.spec:main`): `check` reports what the profiling server would use, and a bare call starts the stdio server (no JDK needed)."""

import sys
from pathlib import Path

import pytest

from frc_mcp_jfr import spec
from frc_mcp_jfr.errors import JfrError
from frc_mcp_jfr.jdk import Jvm


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["mcp-jfr", *argv])
    spec.main()


def _stub_jdk(monkeypatch, jvms):
    async def list_jvms(contains=None):
        return jvms

    monkeypatch.setattr(spec.jdk, "list_jvms", list_jvms)
    monkeypatch.setattr(spec.config, "jdk_tool", lambda name: f"/jdk/bin/{name}")


def test_check_lists_the_recordings_the_tools_and_the_jvms(monkeypatch, rec_dir, capsys):
    _stub_jdk(monkeypatch, [Jvm(101, "first.robot.Main"), Jvm(202, "Glass")])
    (rec_dir / "first.jfr").write_bytes(b"x")
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "check")
    out = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert f"{rec_dir} (1 found)" in out and "/jdk/bin/jfr" in out and "2 running JVM(s) can be recorded" in out
    assert "Note:" not in out  # JFR_DIR is set by the fixture, so there is nothing to note


def test_check_notes_the_default_folder_when_jfr_dir_is_unset(monkeypatch, capsys):
    _stub_jdk(monkeypatch, [])
    monkeypatch.delenv("JFR_DIR", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "check")
    out = capsys.readouterr().out.replace("\n", " ")
    assert exit_info.value.code == 0
    assert str(Path.cwd()) in out and "Note: JFR_DIR is not set" in out and "0 running JVM(s) can be recorded" in out


def test_check_gives_the_jdks_own_message_and_fails(monkeypatch, rec_dir, capsys):
    async def list_jvms(contains=None):
        raise JfrError("The JDK tool `jcmd` was not found.")

    monkeypatch.setattr(spec.jdk, "list_jvms", list_jvms)
    monkeypatch.setattr(spec.config, "jdk_tool", lambda name: f"/jdk/bin/{name}")
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "check")
    assert exit_info.value.code == 1 and "jcmd" in capsys.readouterr().out


def test_a_bad_toolset_stops_before_the_server_starts(monkeypatch, capsys):
    mcp, _ = spec.SPEC.load()

    started: list[bool] = []
    monkeypatch.setattr(mcp, "run", lambda **kwargs: started.append(True))
    monkeypatch.setenv("JFR_TOOLSET", "everything")
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch)
    assert "JFR_TOOLSET" in str(exit_info.value) and not started


def test_without_a_command_the_stdio_server_runs(monkeypatch):
    mcp, _ = spec.SPEC.load()

    started: list[dict] = []
    monkeypatch.setattr(mcp, "run", lambda **kwargs: started.append(kwargs))
    monkeypatch.setenv("JFR_TOOLSET", "core")
    run(monkeypatch)
    assert started == [{"show_banner": False}]  # no SystemExit: main() returns after the server stopped

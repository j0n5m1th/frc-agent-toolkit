"""`frc-mcp demo log` (`frc_mcp_wpilog.demo`): writes `practice.wpilog` where asked, and the log server's reader opens it."""

import pytest

from frc_mcp_core import command
from frc_mcp_wpilog import demo
from frc_mcp_wpilog.logfile import LogFile, load


def test_the_default_file_is_practice_wpilog_in_the_current_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert demo.main([]) == 0
    written = tmp_path / "practice.wpilog"
    assert written.is_file() and demo.DEFAULT_OUT.name == "practice.wpilog" and not demo.DEFAULT_OUT.is_absolute()
    out = capsys.readouterr().out
    assert str(written.resolve()) in out and "frc-mcp wpilog check practice.wpilog" in out  # says where it wrote and what to do next


def test_the_command_writes_a_log_the_reader_opens(tmp_path, capsys):
    out = tmp_path / "mine.wpilog"
    assert command.main(["demo", "log", "--out", str(out)]) == 0
    log = LogFile(out)
    assert log.duration_s == 30.0
    assert str(out.resolve()) in capsys.readouterr().out
    assert load(out) is not None


def test_a_length_outside_the_range_is_refused(tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        demo.main(["--out", str(tmp_path / "x.wpilog"), "--seconds", "5"])
    assert stop.value.code == 2 and "--seconds" in capsys.readouterr().err
    assert not (tmp_path / "x.wpilog").exists()

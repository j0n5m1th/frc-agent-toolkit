"""Command line: `check` lets a student look at logs without an assistant."""

import sys

import pytest

from frc_mcp_wpilog import spec


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["mcp-wpilib-wpilog", *argv])
    spec.main()


def test_check_of_one_log_prints_the_summary_a_student_wants(monkeypatch, log_dir, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "check", "sample.wpilog")
    out = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert "sample.wpilog: 12.0 s" in out and "enabled in auto: 2.0 s to 5.0 s" in out
    assert "enabled in teleop: 6.0 s to 10.0 s" in out and "EventName: Practice Field" in out
    assert "/Fake/Elevator/Current (121)" in out


def test_check_of_a_bad_log_name_reports_it_instead_of_a_traceback(monkeypatch, log_dir, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "check", "missing.wpilog")
    assert exit_info.value.code == 1 and "Cannot open the log" in capsys.readouterr().out


def test_check_without_a_name_shows_where_it_looked_and_what_it_found(monkeypatch, log_dir, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "check")
    out = capsys.readouterr().out
    assert exit_info.value.code == 0 and "sample.wpilog" in out and str(log_dir.resolve()) in out


def test_check_of_an_empty_folder_says_how_to_point_at_logs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        run(monkeypatch, "check")
    assert "Set WPILOG_DIR" in capsys.readouterr().out


def test_check_nudges_to_set_wpilog_dir_when_it_is_unset(monkeypatch, log_dir, capsys):
    monkeypatch.delenv("WPILOG_DIR")
    monkeypatch.chdir(log_dir)
    with pytest.raises(SystemExit):
        run(monkeypatch, "check")
    assert "WPILOG_DIR is not set" in capsys.readouterr().out


def test_arguments_are_validated(monkeypatch, capsys):
    # One self-test name, no aliases: anything else is an argparse choice error.
    for bad in (["extra.wpilog"], ["list", "sample.wpilog"], ["inspect", "sample.wpilog"]):
        capsys.readouterr()
        with pytest.raises(SystemExit) as exit_info:
            run(monkeypatch, *bad)
        assert exit_info.value.code == 2 and "invalid choice" in capsys.readouterr().err


def test_no_command_runs_the_stdio_server(monkeypatch):
    ran = []
    from frc_mcp_wpilog.spec import SPEC

    mcp, _ = SPEC.load()

    monkeypatch.setattr(mcp, "run", lambda **kwargs: ran.append(kwargs))
    run(monkeypatch)
    assert ran == [{"show_banner": False}]


def test_unknown_toolset_exits_before_the_server_starts(monkeypatch):
    ran = []
    from frc_mcp_wpilog.spec import SPEC

    mcp, _ = SPEC.load()

    monkeypatch.setattr(mcp, "run", lambda **kwargs: ran.append(kwargs))
    monkeypatch.setenv("WPILOG_TOOLSET", "everything")
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch)
    assert "WPILOG_TOOLSET" in str(exit_info.value) and not ran

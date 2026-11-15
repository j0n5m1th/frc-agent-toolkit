"""Command line: `check` shows what would run without an assistant."""

import sys

import pytest

from frc_mcp_vscode import spec


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["mcp-wpilib-vscode", *argv])
    spec.main()


def test_check_shows_the_project_team_and_deploy_state(monkeypatch, project, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "check")
    out = capsys.readouterr().out
    assert exit_info.value.code == 0 and "robot" in out and "team 9999" in out and "VSCODE_ALLOW_DEPLOY=1" in out


def test_check_without_a_project_says_how_to_set_one(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VSCODE_PROJECT_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "check")
    assert exit_info.value.code == 1 and "VSCODE_PROJECT_DIR" in capsys.readouterr().out

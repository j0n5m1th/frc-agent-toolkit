"""A server never searches a drive root or the home folder just because it was started there; the same holds when a setting names one."""

from pathlib import Path

import pytest

from frc_mcp_core.folders import AllowedFolders, too_broad
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.folders import Folders
from frc_mcp_jfr import config as jfr_config
from frc_mcp_jfr.errors import JfrError
from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilib.gradle.project import ProjectSource
from frc_mcp_wpilog import config as wpilog_config
from frc_mcp_wpilog.errors import WPILogError

SETTINGS = ("TEST_BROAD_DIR", "OWLET_DIR", "JFR_DIR", "WPILOG_DIR", "SIM_PROJECT_DIR")


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(params=("root", "home"))
def start_in_broad_folder(request, tmp_path, monkeypatch):
    """Start in a drive root or in (what counts as) the home folder; returns that folder."""
    if request.param == "root":
        folder = Path(tmp_path.anchor)
    else:
        folder = tmp_path
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(folder)
    return folder


def test_a_drive_root_and_the_home_folder_are_too_broad(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert too_broad(Path(tmp_path.anchor)) and too_broad(tmp_path)
    assert not too_broad(tmp_path / "robot")


def test_the_shared_rule_refuses_an_implicit_broad_folder_naming_the_setting(start_in_broad_folder):
    with pytest.raises(ValueError, match="TEST_BROAD_DIR is not set.*Set TEST_BROAD_DIR"):
        AllowedFolders("TEST_BROAD_DIR").roots()


def test_an_explicit_setting_naming_a_broad_folder_is_refused_naming_the_setting(start_in_broad_folder, monkeypatch):
    """The plugin sets every folder setting to the project folder, so a project opened in the home folder or a drive root must not search all of it."""
    monkeypatch.setenv("TEST_BROAD_DIR", str(start_in_broad_folder))
    with pytest.raises(ValueError, match="TEST_BROAD_DIR names .*too broad.*Set TEST_BROAD_DIR"):
        AllowedFolders("TEST_BROAD_DIR").roots()


def test_a_narrow_working_directory_is_still_the_default(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    work = tmp_path / "logs"
    work.mkdir()
    monkeypatch.chdir(work)
    assert AllowedFolders("TEST_BROAD_DIR").roots() == [work.resolve()]


def test_ctre_folders_raise_the_ctre_error(start_in_broad_folder):
    with pytest.raises(CtreError, match="OWLET_DIR"):
        Folders("OWLET_DIR", (".hoot",), "hoot file", "owlet_list").roots()


def test_ctre_folders_with_a_default_place_do_not_need_the_working_directory(start_in_broad_folder, tmp_path):
    place = tmp_path / "tunerx"
    place.mkdir()
    folders = Folders("OWLET_DIR", (".hoot",), "hoot file", "owlet_list", default_roots=lambda: [place])
    assert folders.roots() == [place.resolve()]


def test_the_project_source_raises_its_own_error(start_in_broad_folder):
    class SimError(WPILibMCPError):
        pass

    with pytest.raises(SimError, match="SIM_PROJECT_DIR"):
        ProjectSource("SIM_PROJECT_DIR", SimError).roots()


def test_the_profiling_server_raises_its_error(start_in_broad_folder):
    with pytest.raises(JfrError, match="JFR_DIR"):
        jfr_config.roots()


def test_the_log_server_raises_its_error(start_in_broad_folder):
    with pytest.raises(WPILogError, match="WPILOG_DIR"):
        wpilog_config.log_roots()

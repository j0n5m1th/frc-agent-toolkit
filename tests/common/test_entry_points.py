"""Every server and demo is registered as a package entry point, and what is registered matches the folder, the spec and the pyproject."""

import importlib.metadata
import tomllib

import pytest
from repo_layout import FRC

from frc_mcp_core import command

SERVER_DIRS = sorted(p for p in (FRC / "servers").iterdir() if p.is_dir())
DEMOS = {"robot": "frc_mcp_nt4.demo:main", "log": "frc_mcp_wpilog.demo:main"}
SKIPPED_BY_DOCTOR = {"nt4", "systemcore", "ctre-diag", "ctre-caniv", "ctre-tools"}  # they reach a robot, a controller, a device or the internet


def project(folder) -> dict:
    return tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def package_of(folder) -> str:
    return next(p.name for p in folder.iterdir() if p.is_dir() and (p / "__init__.py").exists())


@pytest.mark.parametrize("folder", SERVER_DIRS, ids=lambda f: f.name)
def test_each_server_declares_its_entry_point_under_its_folder_name(folder):
    declared = project(folder)["entry-points"][command.SERVERS_GROUP]
    assert declared == {folder.name: f"{package_of(folder)}.spec:SPEC"}


@pytest.mark.parametrize("folder", SERVER_DIRS, ids=lambda f: f.name)
def test_the_installed_entry_point_loads_a_spec_whose_key_is_the_folder_name(folder):
    point = next(ep for ep in importlib.metadata.entry_points(group=command.SERVERS_GROUP) if ep.name == folder.name)
    assert point.value == f"{package_of(folder)}.spec:SPEC"
    assert point.load().key == folder.name == point.name


def test_the_installed_keys_are_exactly_the_servers_and_none_is_a_command_word():
    assert command.server_keys() == sorted(f.name for f in SERVER_DIRS)
    assert not set(command.server_keys()) & set(command.RESERVED)


@pytest.mark.parametrize("folder", SERVER_DIRS, ids=lambda f: f.name)
def test_the_old_script_name_is_kept_as_an_alias_with_a_note_to_drop_it(folder):
    text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    assert list(project(folder)["scripts"].values()) == [f"{package_of(folder)}.spec:main"]
    assert "drop it next release" in text


def test_the_demos_are_registered_by_the_nt4_and_wpilog_servers():
    declared = {name: value for folder in SERVER_DIRS for name, value in project(folder).get("entry-points", {}).get(command.DEMOS_GROUP, {}).items()}
    assert declared == DEMOS
    assert command.demo_names() == sorted(DEMOS)
    for name, value in DEMOS.items():
        assert callable(importlib.metadata.EntryPoint(name, value, command.DEMOS_GROUP).load())


def test_the_root_package_installs_the_frc_mcp_command():
    root = tomllib.loads((FRC / "pyproject.toml").read_text(encoding="utf-8"))
    assert root["project"]["scripts"] == {"frc-mcp": "frc_mcp_core.command:main"}
    assert "frc-mcp-core" in root["project"]["dependencies"]


def test_doctor_skips_exactly_the_servers_that_leave_this_computer():
    skipped = {key for key in command.server_keys() if command.load_spec(key).doctor_skip}
    assert skipped == SKIPPED_BY_DOCTOR
    assert all(command.load_spec(key).doctor_skip.startswith("it ") for key in skipped)  # the reason reads "skipped  key: it ...; run ..."

"""The shared Gradle code and server settings helpers: project source, build-file facts, live-run registry, flags and capabilities."""

import os
from pathlib import Path

import pytest
from gradle_helpers import write_wrapper

from frc_mcp_core.server_support import env_toolset
from frc_mcp_core.settings import Capability, env_flag, env_number
from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilib.gradle import buildfile
from frc_mcp_wpilib.gradle import run as gradle_run
from frc_mcp_wpilib.gradle.jdk import (
    Jdk,
    describe,
    read_preferences,
    select,
    wpilib_home,
)
from frc_mcp_wpilib.gradle.project import ProjectSource, gradlew


class Oops(WPILibMCPError):
    pass


def make_project(folder, text="plugins { id 'edu.wpi.first.GradleRIO' }"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "build.gradle").write_text(text, encoding="utf-8")
    script = folder / "wrapper.py"  # one long-running line of output, so a wait has something to observe
    script.write_text("import time\nprint(1, flush=True)\ntime.sleep(30)\n", encoding="utf-8")
    write_wrapper(folder, script)
    return folder


def test_project_source_uses_its_own_variable_and_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_DIR", str(tmp_path))
    source = ProjectSource("SOME_DIR", Oops)
    with pytest.raises(Oops, match="SOME_DIR"):
        source.resolve()
    make_project(tmp_path / "robot")
    assert source.resolve().name == "robot" and source.notice() is None
    monkeypatch.delenv("SOME_DIR")
    assert "SOME_DIR is not set" in source.notice()


def test_project_source_checks_numbers_and_properties():
    source = ProjectSource("X", Oops)
    assert source.props_to_args({"a": "1", "flag": ""}) == ["-Pa=1", "-Pflag"]
    with pytest.raises(Oops):
        source.props_to_args({"a": "b&c"})
    os.environ["SOME_NUMBER"] = "5000"
    try:
        with pytest.raises(Oops, match="SOME_NUMBER"):
            env_number("SOME_NUMBER", 1, 1, 10, Oops)
        assert env_number("UNSET_NUMBER_VAR", 7, 1, 10, Oops) == 7
    finally:
        del os.environ["SOME_NUMBER"]


def test_build_file_facts_are_read_from_text_only_when_present():
    text = 'id "org.wpilib.GradleRIO" version "2027.0.0-alpha-7"\nsourceCompatibility = JavaVersion.VERSION_25\n    systemcore(getTargetTypeClass(\'SystemCore\')) {'
    assert buildfile.facts(text) == {"gradlerio": "2027.0.0-alpha-7", "java": 25, "deploy_targets": ["systemcore"]}
    assert buildfile.facts("apply plugin: 'java'") == {}
    assert buildfile.is_wpilib(text) and not buildfile.is_wpilib("apply plugin: 'java'")


def _java_in(home: Path) -> Path:
    """A folder that looks like a JDK to the chooser (it only checks that bin/java is there)."""
    (home / "bin").mkdir(parents=True, exist_ok=True)
    (home / "bin" / ("java.exe" if os.name == "nt" else "java")).write_text("", encoding="utf-8")
    return home


def test_the_chosen_jdk_is_described_with_where_it_came_from(tmp_path):
    assert describe(Jdk(tmp_path / "jdk", "WPILib install")) == f"{tmp_path / 'jdk'} (from WPILib install)"
    assert describe(None) == "JAVA_HOME or PATH"


def test_the_wpilib_folder_is_the_named_one_or_this_systems_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "no-xdg"))  # a Linux machine with the 2027 folder must not change the default asserted here
    monkeypatch.setenv("WPILIB_HOME", str(tmp_path / "wpilib"))
    assert wpilib_home() == tmp_path / "wpilib"
    monkeypatch.setenv("WPILIB_HOME", "   ")  # blank counts as unset
    assert wpilib_home() == (Path("C:/Users/Public/wpilib") if os.name == "nt" else Path.home() / "wpilib")
    monkeypatch.delenv("WPILIB_HOME")
    assert wpilib_home() == (Path("C:/Users/Public/wpilib") if os.name == "nt" else Path.home() / "wpilib")


def test_the_linux_2027_install_folder_is_used_when_it_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("WPILIB_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr("sys.platform", "linux")
    assert wpilib_home() == Path.home() / "wpilib"  # no XDG folder yet: the 2026 default
    (tmp_path / "data" / "wpilib").mkdir(parents=True)
    assert wpilib_home() == tmp_path / "data" / "wpilib"


def test_preferences_are_only_taken_when_they_are_a_json_object(tmp_path):
    prefs = tmp_path / ".wpilib" / "wpilib_preferences.json"
    prefs.parent.mkdir()
    prefs.write_text('{"projectYear": "2027_alpha7", "teamNumber": 9998}', encoding="utf-8")
    assert read_preferences(tmp_path) == {"projectYear": "2027_alpha7", "teamNumber": 9998}
    prefs.write_text("[1, 2]", encoding="utf-8")
    assert read_preferences(tmp_path) == {}  # not an object: as good as missing
    prefs.write_text('{"projectYear": ', encoding="utf-8")
    assert read_preferences(tmp_path) == {}  # broken JSON
    assert read_preferences(tmp_path / "none") == {}  # no file at all


def test_the_jdk_is_chosen_in_order_and_an_explicit_folder_is_checked(tmp_path, monkeypatch):
    mine = _java_in(tmp_path / "my-jdk")
    monkeypatch.setenv("WPILIB_JAVA_HOME", str(mine))
    monkeypatch.setenv("WPILIB_HOME", str(tmp_path / "wpilib"))
    assert select(tmp_path, Oops) == Jdk(mine, "WPILIB_JAVA_HOME")  # 1: an explicit choice wins
    monkeypatch.setenv("WPILIB_JAVA_HOME", str(tmp_path / "no-jdk"))
    with pytest.raises(Oops, match=r"WPILIB_JAVA_HOME.*no bin/java"):
        select(tmp_path, Oops)
    monkeypatch.delenv("WPILIB_JAVA_HOME")
    # 2: the JDK the installer put next to this project's year
    install = tmp_path / "wpilib"
    _java_in(install / "2027_alpha7" / "jdk")
    assert select(tmp_path, Oops, prefs={"projectYear": "2027_alpha7"}) == Jdk(install / "2027_alpha7" / "jdk", "WPILib install")
    assert select(tmp_path, Oops, prefs={"projectYear": "2026"}) is None  # that year has no jdk: 3, leave the environment alone
    assert select(tmp_path, Oops, prefs={}) is None  # no year either
    # the project's own file is read when the caller has not read it yet
    (tmp_path / ".wpilib").mkdir()
    (tmp_path / ".wpilib" / "wpilib_preferences.json").write_text('{"projectYear": "2027_alpha7"}', encoding="utf-8")
    assert select(tmp_path, Oops) == Jdk(install / "2027_alpha7" / "jdk", "WPILib install")


def test_flags_are_strict_and_capabilities_name_the_setting(monkeypatch):
    assert env_flag("FLAG_UNSET_XYZ", True, Oops) is True
    monkeypatch.setenv("FLAG_X", " On ")
    assert env_flag("FLAG_X", False, Oops) is True
    monkeypatch.setenv("FLAG_X", "maybe")
    with pytest.raises(Oops, match="FLAG_X must be a boolean"):
        env_flag("FLAG_X", False, Oops)
    cap = Capability("CAP_X", "Deploying", Oops, why="it changes the robot")
    monkeypatch.delenv("CAP_X", raising=False)
    assert cap.enabled() is False
    # the refusing tool and the status report read the one sentence `refusal()` gives
    assert cap.refusal() == "Deploying is off. Set CAP_X=1 on the MCP server to allow it (it changes the robot)."
    with pytest.raises(Oops, match=r"Deploying is off. Set CAP_X=1 .*\(it changes the robot\)"):
        cap.require()
    monkeypatch.setenv("CAP_X", "1")
    cap.require()
    assert cap.refusal() is None
    monkeypatch.setenv("CAP_X", "maybe")
    with pytest.raises(Oops):
        cap.enabled()


def test_toolset_is_read_from_the_named_variable(monkeypatch):
    monkeypatch.delenv("TS_X", raising=False)
    assert env_toolset("TS_X", Oops) == "full"
    monkeypatch.setenv("TS_X", "core")
    assert env_toolset("TS_X", Oops) == "core"
    monkeypatch.setenv("TS_X", "everything")
    with pytest.raises(Oops, match="TS_X"):
        env_toolset("TS_X", Oops)


async def test_live_runs_are_registered_for_exit_cleanup_unless_they_outlive_the_server(tmp_path):
    project = make_project(tmp_path / "robot")
    wrapper = gradlew(project)
    ordinary, window = gradle_run.GradleRun(project, []), gradle_run.GradleRun(project, [])
    try:
        await ordinary.launch(wrapper, 60, error=Oops)
        await window.launch(wrapper, None, outlive_server=True, error=Oops)
        assert ordinary in gradle_run._live and window not in gradle_run._live
    finally:
        await ordinary.stop()
        await window.stop()
    assert ordinary not in gradle_run._live


async def test_a_run_is_tracked_for_server_death_unless_it_outlives_the_server(tmp_path, monkeypatch):
    tracked: list[int] = []
    monkeypatch.setattr(gradle_run, "track_child", lambda pid: tracked.append(pid) or True)
    project = make_project(tmp_path / "robot")
    wrapper = gradlew(project)
    ordinary, window = gradle_run.GradleRun(project, []), gradle_run.GradleRun(project, [])
    try:
        await ordinary.launch(wrapper, 60, error=Oops)
        await window.launch(wrapper, None, outlive_server=True, error=Oops)
        assert tracked == [ordinary.pid]
    finally:
        await ordinary.stop()
        await window.stop()

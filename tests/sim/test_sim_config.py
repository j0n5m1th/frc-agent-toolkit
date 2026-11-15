"""What the simulation server refuses to launch, and how it finds the robot project."""

import pytest
from sim_helpers import make_project

from frc_mcp_sim import config
from frc_mcp_sim.errors import SimError
from frc_mcp_wpilib.gradle.console import Console, fold


def test_only_simulate_tasks_are_allowed():
    assert config.check_task("simulateJavaAgent") == "simulateJavaAgent"
    for bad in ("build", "deploy", "simulateJava; calc", "simulate Java", "../simulateJava", ""):
        with pytest.raises(SimError, match="simulate"):
            config.check_task(bad)


def test_properties_become_plain_p_arguments():
    assert config.source.props_to_args({"mode": "auto:Middle", "headless": "", "stickScript": "1-3:ly=-1;5-7:lx=-0.5,rx=0.3"}) == [
        "-Pmode=auto:Middle", "-Pheadless", "-PstickScript=1-3:ly=-1;5-7:lx=-0.5,rx=0.3"]
    assert config.source.props_to_args(None) == []


@pytest.mark.parametrize("props", [{"a b": "x"}, {"-x": "y"}, {"mode": "a&b"}, {"mode": "a b"}, {"mode": '"x"'}, {"mode": "%PATH%"}, {"mode": "x" * 201}])
def test_properties_with_shell_characters_are_refused(props):
    with pytest.raises(SimError):
        config.source.props_to_args(props)


def test_a_project_needs_a_wrapper_and_a_gradlerio_build_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_PROJECT_DIR", str(tmp_path))
    make_project(tmp_path / "other", text="apply plugin: 'java'")
    with pytest.raises(SimError, match="No WPILib project"):
        config.source.resolve()
    with pytest.raises(SimError, match="not a WPILib project"):
        config.source.resolve("other")
    make_project(tmp_path / "robot")
    assert config.source.resolve().name == "robot"


def test_several_projects_need_a_choice_and_outside_folders_are_refused(tmp_path, monkeypatch):
    inside = tmp_path / "in"
    monkeypatch.setenv("SIM_PROJECT_DIR", str(inside))
    make_project(inside / "a")
    make_project(inside / "b")
    make_project(tmp_path / "outside")
    with pytest.raises(SimError, match="pass `project`"):
        config.source.resolve()
    assert config.source.resolve("b").name == "b"
    with pytest.raises(SimError, match="outside the folders"):
        config.source.resolve(str(tmp_path / "outside"))
    with pytest.raises(SimError, match="No folder named"):
        config.source.resolve("missing")


def test_settings_are_validated(monkeypatch):
    monkeypatch.setenv("SIM_NT_PORT", "abc")
    with pytest.raises(SimError, match="SIM_NT_PORT"):
        config.nt_port()
    monkeypatch.setenv("SIM_TASK", "build")
    with pytest.raises(SimError, match="simulate"):
        config.default_task()


def test_the_console_is_bounded_and_keeps_its_counts():
    console = Console()
    for i in range(6000):
        console.add(f"Warning: tick {i}")
    console.add("Exception in thread main")
    assert console.total == 6001 and console.dropped == 1001
    assert console.warnings == 6000 and console.errors == 1
    assert len(console.add("x" * 1000).text) == 301


def test_a_skipped_over_limit_line_still_counts_and_keeps_since_working():
    console = Console()
    console.add("first")
    console.note_skipped()  # the reader's readline hit the stream limit here
    console.add("third")
    assert console.total == 3 and console.dropped == 1  # the skipped line counts once, as a line that did not survive
    assert [line.seq for line in console.select()] == [1, 3]  # the gap is kept, so `since` still indexes correctly
    assert [line.seq for line in console.select(since=1)] == [3]


def test_fold_ignores_numbers_and_keeps_first_seen_order():
    console = Console()
    for text in ("Loop 0.005s overrun 1", "other", "Loop 0.007s overrun 2"):
        console.add(text)
    assert fold(console.select()) == ["1 Loop 0.005s overrun 1 (x2)", "2 other"]

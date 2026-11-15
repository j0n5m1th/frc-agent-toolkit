"""The project's own files read directly: the preferences file, build.gradle facts, the JDK choice and the vendordeps folder."""

import json
import os
from pathlib import Path

import pytest

from frc_mcp_vscode.errors import VSCodeError
from frc_mcp_vscode.project import (
    build_facts,
    check_team,
    check_vendordep_name,
    info,
    install_vendordep,
    offline_source,
    preferences,
    read_json,
    remove_vendordep,
    set_team,
    team_number,
    vendordeps,
)

PREFS_NAME = Path(".wpilib") / "wpilib_preferences.json"


def test_the_preferences_file_is_read_strictly(project):
    assert preferences(project) == {"enableCppIntellisense": False, "currentLanguage": "java",
                                    "projectYear": "2027_alpha7", "teamNumber": 9999}
    with pytest.raises(VSCodeError, match="does not exist"):
        read_json(project / ".wpilib" / "nothing.json")
    (project / "list.json").write_text("[]", encoding="utf-8")
    with pytest.raises(VSCodeError, match="not a JSON object"):
        read_json(project / "list.json")
    (project / "broken.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(VSCodeError, match="Cannot read"):
        read_json(project / "broken.json")
    with pytest.raises(VSCodeError, match="Cannot read"):
        read_json(project)  # a folder is not a JSON file either


def test_the_team_number_has_to_be_a_plain_number(project):
    assert team_number(project) == 9999
    assert set_team(project, 9998) == 9999
    assert json.loads((project / PREFS_NAME).read_text(encoding="utf-8"))["teamNumber"] == 9998
    assert team_number(project) == 9998
    for bad in (0, 25600):
        with pytest.raises(VSCodeError, match="Team number must be from 1 to 25599"):
            check_team(bad)
    # a number the file stored as text is not set for our purposes, and has no old value to report
    (project / PREFS_NAME).write_text(json.dumps({"projectYear": "2027_alpha7", "teamNumber": "9999"}), encoding="utf-8")
    with pytest.raises(VSCodeError, match="is not set"):
        team_number(project)
    assert set_team(project, 7) is None
    assert team_number(project) == 7
    # a JSON boolean is not a team number either, although Python counts True as an int: no `was: true` in the reply
    (project / PREFS_NAME).write_text(json.dumps({"projectYear": "2027_alpha7", "teamNumber": True}), encoding="utf-8")
    with pytest.raises(VSCodeError, match="is not set"):
        team_number(project)
    assert set_team(project, 8) is None
    assert team_number(project) == 8


def test_the_build_file_gives_the_versions_and_targets(project):
    assert build_facts(project) == {"gradlerio": "2027.0.0-alpha-7", "java": 25, "deploy_targets": ["systemcore"]}
    (project / "build.gradle").write_text("plugins {\n}\n", encoding="utf-8")
    assert build_facts(project) == {}


def test_the_info_answer_carries_every_file_in_one_read(project):
    answer = info(project)
    assert answer["project"] == "robot" and answer["team"] == 9999
    assert answer["year"] == "2027_alpha7" and answer["language"] == "java"
    assert answer["jdk"] == {"home": str(Path(os.environ["WPILIB_HOME"]) / "2027_alpha7" / "jdk"),
                             "from": "WPILib install"}
    assert answer["gradlerio"] == "2027.0.0-alpha-7" and answer["java"] == 25
    assert answer["deploy_targets"] == ["systemcore"]
    assert answer["installed"] == {"CommandsV2": "1.0.0"}
    assert answer["offline_available"] == {"AprilTag": "2.0.0", "CommandsV3": "1.0.0"}
    assert answer["offline_differs"] == {"CommandsV2": "1.0.0 -> 1.0.1"}


def test_the_jdk_falls_back_to_the_environment(project, monkeypatch):
    # an install folder with no jdk beside the year: nothing chosen, JAVA_HOME and PATH as started
    monkeypatch.setenv("WPILIB_HOME", str(project.parent))
    assert info(project)["jdk"] == "JAVA_HOME or PATH"


def test_the_offline_folder_follows_the_project_year(project):
    home = Path(os.environ["WPILIB_HOME"])
    assert offline_source(project) == home / "2027_alpha7" / "vendordeps"
    assert offline_source(project, {"projectYear": "2025"}) == home / "2025" / "vendordeps"
    assert offline_source(project, {}) == home / "vendordeps"  # no year, the install root itself


def test_an_unreadable_vendordep_is_skipped_but_still_removable(project):
    (project / "vendordeps" / "Broken.json").write_text("{oops", encoding="utf-8")
    listed = vendordeps(project, {"projectYear": "2027_alpha7"})
    assert listed["installed"] == {"CommandsV2": "1.0.0"}  # the broken file is not listed
    assert remove_vendordep(project, "Broken") == {"name": "Broken", "removed": None}
    assert not (project / "vendordeps" / "Broken.json").exists()


def test_a_library_can_be_copied_in_and_taken_out_again(project):
    assert install_vendordep(project, "AprilTag") == {"name": "AprilTag", "version": "2.0.0", "replaced": None}
    assert json.loads((project / "vendordeps" / "AprilTag.json").read_text(encoding="utf-8"))["uuid"] == "u-tag"
    assert install_vendordep(project, "AprilTag") == {"name": "AprilTag", "version": "2.0.0", "replaced": "2.0.0"}
    assert remove_vendordep(project, "AprilTag") == {"name": "AprilTag", "removed": "2.0.0"}
    assert not (project / "vendordeps" / "AprilTag.json").exists()
    with pytest.raises(VSCodeError, match="is not in vendordeps"):
        remove_vendordep(project, "AprilTag")
    with pytest.raises(VSCodeError, match="No offline vendordep"):
        install_vendordep(project, "Nope")
    for bad in ("", "bad name", ".Hidden"):
        with pytest.raises(VSCodeError, match="is not allowed"):
            check_vendordep_name(bad)


def test_a_library_that_clashes_with_an_installed_one_is_refused(project):
    with pytest.raises(VSCodeError, match="CommandsV3 conflicts with the installed CommandsV2"):
        install_vendordep(project, "CommandsV3")
    remove_vendordep(project, "CommandsV2")
    assert install_vendordep(project, "CommandsV3") == {"name": "CommandsV3", "version": "1.0.0", "replaced": None}

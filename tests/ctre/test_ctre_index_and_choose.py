"""Versions, the index, and choosing a tool version for a channel, a project or a log."""

import copy
import json

import pytest
from ctre_helpers import INDEX, make_project

from frc_mcp_ctre import choose
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.fetch import snapshot
from frc_mcp_ctre.index import parse_index
from frc_mcp_ctre.project import read_project
from frc_mcp_ctre.versions import parse_version


def versions_of(items):
    return [v.version for v in items]


# -- versions --------------------------------------------------------------------------------------------------------


def test_versions_order_by_number_and_a_release_beats_its_own_prerelease():
    texts = ["26.70.0-alpha-2", "26.3.0", "26.3.0-beta-1", "26.10.0", "1.0.1.1", "26.3.0-beta-10", "26.3.0-beta-2"]
    ordered = sorted(texts, key=lambda t: parse_version(t).sort_key())
    assert ordered == ["1.0.1.1", "26.3.0-beta-1", "26.3.0-beta-2", "26.3.0-beta-10", "26.3.0", "26.10.0", "26.70.0-alpha-2"]


def test_a_version_knows_its_season_and_whether_it_is_a_prerelease():
    assert parse_version("26.1.3").season == 2026 and not parse_version("26.1.3").prerelease
    assert parse_version("26.50.0-alpha-1").prerelease
    assert parse_version("1.0.1.1").season is None


def test_text_that_is_not_a_version_is_refused_in_plain_words():
    with pytest.raises(CtreError, match="not a CTRE version"):
        parse_version("latest")


# -- the index -------------------------------------------------------------------------------------------------------


def test_the_index_lists_versions_newest_first_and_knows_each_platform_build():
    index = parse_index(json.dumps(INDEX))
    assert versions_of(index.versions("OWLET")) == ["26.70.0", "26.3.0", "26.1.0", "26.0.0", "25.4.1"]  # any case
    download = index.find("owlet", "26.3.0").download("linuxx86-64")
    assert download.url.endswith("owlet-26.3.0-linuxx86-64") and len(download.md5) == 32
    assert index.find("passerine", "1.0.0").download("windowsx86-64") is None
    assert index.find("owlet", "26.3.0").platforms() == ["linuxx86-64", "windowsx86-64"]


def test_a_channel_covers_its_seasons_levels_up_to_its_own():
    index = parse_index(json.dumps(INDEX))
    assert index.compliancy_range("2026") == (18, 19)
    assert index.compliancy_range("2026-beta-1") == (18, 18)
    assert index.compliancy_range("2027-alpha-7") == (20, 21)
    assert index.channels_for(19) == ["2026"]
    with pytest.raises(CtreError, match="no channel 'nope'"):
        index.channel("nope")
    with pytest.raises(CtreError, match="no tool 'nope'.*owlet"):
        index.versions("nope")


@pytest.mark.parametrize("damage", [lambda d: d.pop("Tools"), lambda d: d["Tools"][0].pop("Items"), lambda d: d["ChannelCompliancy"][0].pop("Name"),
                                    lambda d: d["Tools"][0]["Items"][0].pop("Urls"), lambda d: d.pop("LatestChannel")])
def test_an_index_that_changed_shape_is_reported_not_guessed_at(damage):
    broken = copy.deepcopy(INDEX)
    damage(broken)
    with pytest.raises(CtreError, match="looks different than expected"):
        parse_index(json.dumps(broken))


def test_text_that_is_not_json_is_reported():
    with pytest.raises(CtreError, match="not valid JSON"):
        parse_index("<html>")


def test_the_snapshot_shipped_with_the_package_parses_and_only_points_at_ctre():
    index = snapshot()
    assert "owlet" in index.tools and index.latest_channel in [c.name for c in index.channels]
    urls = [u for items in index.tools.values() for item in items for k, u in item.urls.items() if not k.endswith(("-md5", "-sha1"))]
    assert urls and all(u.startswith("https://redist.ctr-electronics.com/") for u in urls)


# -- choosing --------------------------------------------------------------------------------------------------------


def test_a_channel_gets_the_newest_tool_of_its_own_season_never_a_later_alphas():
    index = parse_index(json.dumps(INDEX))
    assert choose.for_channel(index, "owlet", "2026").version == "26.3.0"
    assert choose.for_channel(index, "owlet", "2026-beta-1").version == "26.0.0"
    assert choose.for_channel(index, "owlet", "2027-alpha-7").version == "26.70.0"
    assert choose.for_channel(index, "owlet", "2025").version == "25.4.1"


def test_a_prerelease_is_only_chosen_for_a_prerelease_channel():
    index = parse_index(json.dumps(INDEX))
    assert choose.for_channel(index, "PhoenixDiagnosticsProgram", "2026").version == "26.3.0"  # 26.4.0-beta-1 is the same level but a beta
    assert choose.for_channel(index, "PhoenixDiagnosticsProgram", "2027-alpha-7").version == "26.70.0-alpha-2"


def test_a_platform_without_a_build_is_skipped():
    index = parse_index(json.dumps(INDEX))
    assert choose.for_channel(index, "owlet", "2026", "linuxx86-64").version == "26.3.0"
    assert choose.for_channel(index, "owlet", "2026", "macosuniversal") is None


def test_a_tool_without_compliancy_gets_its_newest_release():
    index = parse_index(json.dumps(INDEX))
    assert choose.for_channel(index, "caniv", "2026").version == "1.1.1.0"


def test_a_log_needs_a_tool_of_exactly_its_level_and_gets_the_newest_build_of_it():
    index = parse_index(json.dumps(INDEX))
    assert choose.for_compliancy(index, "owlet", 19).version == "26.3.0"
    assert choose.for_compliancy(index, "owlet", 18).version == "26.0.0"
    assert choose.for_compliancy(index, "owlet", 21).version == "26.70.0"
    assert choose.for_compliancy(index, "owlet", 20) is None  # no "close enough" level: a neighbouring level refuses the log or returns almost nothing
    assert choose.for_compliancy(index, "owlet", 22) is None


def test_a_release_beats_a_prerelease_at_the_same_level_and_a_missing_platform_gets_nothing():
    index = parse_index(json.dumps(INDEX))
    assert choose.for_compliancy(index, "PhoenixDiagnosticsProgram", 19).version == "26.3.0"  # not the 26.4.0-beta-1 at the same level
    assert choose.for_compliancy(index, "PhoenixDiagnosticsProgram", 21).version == "26.70.0-alpha-2"  # only a prerelease exists at this level
    assert choose.for_compliancy(index, "owlet", 19, "macosuniversal") is None


def test_a_project_on_a_listed_phoenix_release_gets_an_exact_fit(tmp_path):
    index = parse_index(json.dumps(INDEX))
    facts = read_project(make_project(tmp_path / "robot", "26.1.3"))
    fit = choose.project_fit(index, facts)
    assert (fit.channel, fit.compliancy, fit.exact) == ("2026", 19, True)
    assert choose.best_for(index, "PhoenixDiagnosticsProgram", fit, facts, "windowsx86-64").version == "26.1.3"  # a `match` tool follows the project
    assert choose.best_for(index, "owlet", fit, facts, "windowsx86-64").version == "26.3.0"


def test_a_phoenix_release_unknown_to_the_index_falls_back_to_its_season(tmp_path):
    index = parse_index(json.dumps(INDEX))
    fit = choose.project_fit(index, read_project(make_project(tmp_path / "robot", "26.9.9")))
    assert (fit.channel, fit.exact) == ("2026", False) and "26.9.9" in fit.basis


def test_a_project_without_phoenix_uses_its_year_and_no_project_uses_the_latest_channel(tmp_path):
    index = parse_index(json.dumps(INDEX))
    assert choose.project_fit(index, read_project(make_project(tmp_path / "robot", None, year="2025"))).channel == "2025"
    assert choose.project_fit(index, None).channel == "2026"
    assert choose.project_fit(index, read_project(make_project(tmp_path / "old", None, year="2019"))).channel == "2026"  # no 2019 channel


# -- the project -----------------------------------------------------------------------------------------------------


def test_project_facts_come_from_vendordeps_preferences_and_gradle(tmp_path):
    facts = read_project(make_project(tmp_path / "robot", "26.1.3"))
    assert (facts.year, facts.team, facts.gradlerio, facts.phoenix6) == ("2026", 9999, "2026.2.1", "26.1.3")
    assert facts.ctre_vendordeps == {"CTRE-Phoenix (v6)": "26.1.3"} and facts.notes == []


def test_a_project_without_phoenix_says_so(tmp_path):
    facts = read_project(make_project(tmp_path / "robot", None))
    assert facts.phoenix6 is None and "No Phoenix vendordep" in facts.notes[0]


def test_a_folder_that_is_not_a_robot_project_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(CtreError, match="does not look like a WPILib robot project.*Open Claude Code.*CTRE_PROJECT_DIR"):
        read_project(tmp_path / "empty")
    with pytest.raises(CtreError, match="does not exist"):
        read_project(tmp_path / "missing")


def test_a_broken_vendordep_file_is_skipped_not_fatal(tmp_path):
    folder = make_project(tmp_path / "robot", "26.1.3")
    (folder / "vendordeps" / "Broken.json").write_text("{nope", encoding="utf-8")
    assert read_project(folder).phoenix6 == "26.1.3"


@pytest.mark.parametrize("version", ["../..", "1.0/../../x", "1.0/x", "C:evil", "1.0 x", "..", "", "a:b", "x" * 80])
def test_an_index_version_that_cannot_be_a_folder_name_is_left_out_not_the_whole_index(version, caplog):
    broken = copy.deepcopy(INDEX)
    clean = parse_index(json.dumps(broken))
    broken["Tools"][0]["Items"][0]["Version"] = version
    index = parse_index(json.dumps(broken))
    assert index.skipped_versions == 1 and clean.skipped_versions == 0
    assert len(index.tools[broken["Tools"][0]["Name"]]) == len(clean.tools[broken["Tools"][0]["Name"]]) - 1
    assert all(v.version != version for v in index.tools[broken["Tools"][0]["Name"]])
    assert index.channels == clean.channels and set(index.tools) == set(clean.tools)  # everything else is kept
    assert "unusable version" in caplog.text


def test_a_version_missing_from_an_index_entry_is_still_an_error():
    broken = copy.deepcopy(INDEX)
    del broken["Tools"][0]["Items"][0]["Version"]
    with pytest.raises(CtreError, match="has no 'Version'"):
        parse_index(json.dumps(broken))

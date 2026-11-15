"""The CTRE code the file-handling servers share: allowed folders, new output files, finding the installed tool, platform notes, the firmware index."""

import json
import os
from pathlib import Path

import pytest
from ctre_helpers import fake_install

from frc_mcp_ctre import catalog
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.firmware_index import device_releases, load_firmware_index
from frc_mcp_ctre.folders import Folders, new_output_path
from frc_mcp_ctre.resolve import missing_message, newest_installed
from frc_mcp_ctre.status import current_status

MUSIC = Folders("TEST_MUSIC_DIR", (".mid", ".midi"), "MIDI file", "music_list", max_mb_env="TEST_MUSIC_MAX_MB", default_max_mb=1)


@pytest.fixture
def music(tmp_path, monkeypatch):
    folder = tmp_path / "music"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.mid").write_bytes(b"MThd")
    (folder / "sub" / "b.MIDI").write_bytes(b"MThd")
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("TEST_MUSIC_DIR", str(folder))
    monkeypatch.delenv("TEST_MUSIC_MAX_MB", raising=False)
    return folder


# -- Folders ---------------------------------------------------------------------------------------------------------


def test_find_lists_only_the_right_suffixes_newest_first_and_skips_hidden_and_build_folders(music):
    (music / ".git").mkdir()
    (music / ".git" / "c.mid").write_bytes(b"x")
    os.utime(music / "a.mid", (1000, 1000))
    assert [p.name for p in MUSIC.find()] == ["b.MIDI", "a.mid"]
    assert [p.name for p in MUSIC.find("A.M")] == ["a.mid"]


def test_several_folders_can_be_named_and_the_default_is_the_start_folder(music, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("TEST_MUSIC_DIR", os.pathsep.join([str(music), str(other)]))
    assert MUSIC.roots() == [music.resolve(), other.resolve()] and MUSIC.notice() is None
    monkeypatch.delenv("TEST_MUSIC_DIR")
    assert MUSIC.roots() == [Path.cwd().resolve()] and "TEST_MUSIC_DIR is not set" in MUSIC.notice()


def test_a_default_other_than_the_start_folder_can_be_given(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_FW_DIR", raising=False)
    fw = Folders("TEST_FW_DIR", (".crf",), "firmware file", "fw_list", default_roots=lambda: [tmp_path])
    assert fw.roots() == [tmp_path.resolve()] and str(tmp_path) in fw.notice()
    empty = Folders("TEST_FW_DIR", (".crf",), "firmware file", "fw_list", default_roots=lambda: [])
    assert empty.roots() == [Path.cwd().resolve()]


def test_resolve_takes_a_name_a_relative_path_or_an_absolute_path_inside_the_folders(music):
    assert MUSIC.resolve("a.mid") == (music / "a.mid").resolve()
    assert MUSIC.resolve("b.MIDI") == (music / "sub" / "b.MIDI").resolve()  # found by name a few folders down
    assert MUSIC.resolve(str(music / "a.mid")) == (music / "a.mid").resolve()


def test_resolve_refuses_the_wrong_kind_a_file_outside_the_folders_a_missing_file_and_an_ambiguous_name(music, tmp_path):
    with pytest.raises(CtreError, match=r"Only \.mid/\.midi files can be read"):
        MUSIC.resolve("notes.txt")
    outside = tmp_path / "outside.mid"
    outside.write_bytes(b"x")
    with pytest.raises(CtreError, match="outside the folders this server may read.*project folder.*set TEST_MUSIC_DIR"):
        MUSIC.resolve(str(outside))
    with pytest.raises(CtreError, match="No MIDI file named 'zzz.mid'.*Use music_list"):
        MUSIC.resolve("zzz.mid")
    (music / "sub" / "a.mid").write_bytes(b"MThd")
    (music / "sub2").mkdir()
    (music / "sub2" / "a.mid").write_bytes(b"MThd")
    (music / "a.mid").unlink()
    with pytest.raises(CtreError, match="matches 2 MIDI files"):
        MUSIC.resolve("a.mid")


def test_resolve_refuses_a_file_over_the_size_limit(music, monkeypatch):
    (music / "big.mid").write_bytes(b"x" * (2 * 2**20))
    with pytest.raises(CtreError, match="over the TEST_MUSIC_MAX_MB limit"):
        MUSIC.resolve("big.mid")
    monkeypatch.setenv("TEST_MUSIC_MAX_MB", "5")
    assert MUSIC.resolve("big.mid").name == "big.mid"
    monkeypatch.setenv("TEST_MUSIC_MAX_MB", "zero")
    with pytest.raises(CtreError, match="TEST_MUSIC_MAX_MB must be a number"):
        MUSIC.resolve("a.mid")


def test_listing_is_the_shared_reply_shape(music, monkeypatch):
    out = MUSIC.listing(None, 1, lambda path: {"n": path.name[0]})
    out = MUSIC.listing(None, 1, lambda path: {"n": path.name[0]}, extra_cols=("n",))
    assert out["total"] == 2 and len(out["rows"]) == 1 and out["omitted"] == 1 and "hint" not in out
    assert out["cols"] == ["file", "mb", "modified", "n"] and len(out["rows"][0]) == 4  # column names once; each file is a short row
    assert out["rows"][0][3] == Path(out["rows"][0][0]).name[0] and isinstance(out["rows"][0][1], float)
    assert MUSIC.listing("zzz", 5) == {"total": 0, "cols": ["file", "mb", "modified"], "rows": []}  # the shape is the same with nothing to list
    monkeypatch.delenv("TEST_MUSIC_DIR")
    assert "TEST_MUSIC_DIR is not set" in MUSIC.listing(None, 5)["hint"]


# -- new_output_path -------------------------------------------------------------------------------------------------


def test_a_new_output_goes_beside_its_source_with_the_suffix_and_never_over_a_file(tmp_path):
    source = tmp_path / "song.mid"
    assert new_output_path(source, None, ".chrp") == tmp_path / "song.chrp"
    assert new_output_path(source, "demo", ".chrp") == tmp_path / "demo.chrp"
    assert new_output_path(source, "demo.CHRP", ".chrp") == tmp_path / "demo.CHRP"
    (tmp_path / "song.chrp").write_bytes(b"precious")
    with pytest.raises(CtreError, match="never overwritten"):
        new_output_path(source, None, ".chrp")


@pytest.mark.parametrize("name", ["../x", "sub/x", "..", "."])
def test_an_output_name_cannot_leave_the_folder(tmp_path, name):
    with pytest.raises(CtreError, match="plain file name"):
        new_output_path(tmp_path / "song.mid", name, ".chrp")


# -- finding the installed tool --------------------------------------------------------------------------------------


def test_the_newest_installed_version_is_used(tools_dir, index_file):
    fake_install(tools_dir, "passerine", "passerine", "0.9.0")
    fake_install(tools_dir, "passerine", "passerine", "1.0.0")
    assert newest_installed("passerine").version == "1.0.0"


def test_a_missing_tool_says_how_to_install_it_and_never_touches_the_network(index_file):
    with pytest.raises(CtreError, match="No passerine is installed.*ctre_install tool=passerine.*CTRE_ALLOW_INSTALL=1"):
        newest_installed("PASSERINE")  # the index's spelling is used whatever the case


def test_caniv_on_macos_is_refused_with_ctres_note_not_the_install_hint(monkeypatch, index_file):
    monkeypatch.setenv("CTRE_PLATFORM", "macosuniversal")
    with pytest.raises(CtreError, match=r"caniv cannot be used on this computer \(macosuniversal\).*not available on macOS") as caught:
        newest_installed("caniv")
    assert "ctre_install" not in str(caught.value)
    assert "No passerine is installed" in missing_message("passerine")  # other tools on macOS get the normal message


def test_the_catalog_knows_what_each_tool_is_for_and_which_platforms_ctre_excludes():
    assert catalog.platform_note("caniv", "macosuniversal").startswith("CTRE's documentation says caniv is not available on macOS")
    assert catalog.platform_note("caniv", "windowsx86-64") is None and catalog.platform_note("owlet", "macosuniversal") is None
    assert "MIDI" in catalog.info("passerine").purpose and "CANivore" in catalog.info("caniv").purpose


def test_status_explains_a_tool_with_no_build_for_this_platform(monkeypatch, index_file):
    monkeypatch.setenv("CTRE_PLATFORM", "macosuniversal")
    rows = {r["tool"]: r for r in current_status()["tools"]}
    assert rows["caniv"]["status"] == "no_build" and "not available on macOS" in rows["caniv"]["note"]


def test_a_tool_with_no_pairing_falls_back_to_its_newest_release_from_another_channel_and_says_so(monkeypatch, index_file):
    monkeypatch.setenv("CTRE_PLATFORM", "macosuniversal")  # the made-up index builds passerine for macOS only, and only at the 2027 alpha level
    row = {r["tool"]: r for r in current_status()["tools"]}["passerine"]
    assert (row["status"], row["recommended"]) == ("missing", "1.0.0")
    assert row["note"].startswith("Not in channel 2026: the newest passerine is from channel 2027-alpha-7")


def test_an_installed_copy_of_a_tool_with_no_pairing_counts_as_current_whatever_its_channel(tools_dir, monkeypatch, index_file):
    monkeypatch.setenv("CTRE_PLATFORM", "macosuniversal")
    fake_install(tools_dir, "passerine", "passerine", "1.0.0", "macosuniversal")
    row = {r["tool"]: r for r in current_status()["tools"]}["passerine"]
    assert (row["use"], row["status"]) == ("1.0.0", "ok") and "other" not in row


# -- the firmware index ----------------------------------------------------------------------------------------------

FIRMWARE_INDEX = {"Version": "2.1.0.0", "Latest": {"v6": "2026"}, "Releases": {"v6": {
    "2027-alpha-7": [{"Device": "CANivore", "LatestRelease": "CANivore-26.70.0.0-PostSeason2026.crf", "LatestVersion": "26.70.0.0"}],
    "2026": [{"Device": "TalonFXS", "LatestVersion": "26.3.0.1", "LatestRelease": "x.crf"},
             {"Device": "CANivore", "LatestRelease": "CANivore-26.1.0.0-Season2026.crf", "LatestVersion": "26.1.0.0"}],
    "2025": [{"Device": "CANivore", "LatestRelease": "CANivore-25.4.0.0-Season2025.crf", "LatestVersion": "25.4.0.0", "CriticalVersion": "25.3.0.0",
              "Errata": "Update available that fixes a critical silicon vendor errata." + " x" * 100}],
    "2024": [{"Device": "CANivore", "LatestVersion": "24.0.1.0", "LatestRelease": "old.crf"}]}}}


def tunerx(tmp_path, monkeypatch, data=FIRMWARE_INDEX):
    packages = tmp_path / "Packages"
    cache = packages / "CTRElectronics.PhoenixTunerXPreview_x" / "LocalCache"
    cache.mkdir(parents=True)
    if data is not None:
        (cache / "firmware-index.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("CTRE_TUNERX_DIR", str(packages))
    return cache


def test_the_firmware_index_is_read_from_tuner_xs_saved_copy_and_summarised_per_season(tmp_path, monkeypatch):
    tunerx(tmp_path, monkeypatch)
    releases = device_releases(load_firmware_index(), "canivore")
    assert list(releases) == ["2027-alpha-7", "2026", "2025"]  # the newest three seasons, in the index's order
    assert releases["2026"] == {"latest": "26.1.0.0", "file": "CANivore-26.1.0.0-Season2026.crf"}
    assert releases["2025"]["critical"] == "25.3.0.0" and len(releases["2025"]["errata"]) <= 160


def test_without_a_saved_or_with_a_broken_firmware_index_there_is_just_nothing(tmp_path, monkeypatch):
    assert load_firmware_index() is None and device_releases(None, "CANivore") == {}  # CTRE_TUNERX_DIR is off in these tests
    cache = tunerx(tmp_path, monkeypatch, data=None)
    assert load_firmware_index() is None
    (cache / "firmware-index.json").write_text("{nope", encoding="utf-8")
    assert load_firmware_index() is None
    (cache / "firmware-index.json").write_text('{"Releases": 3}', encoding="utf-8")
    assert load_firmware_index() is None


def test_a_default_place_with_a_label_is_named_in_the_hint_instead_of_listing_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_FW_DIR", raising=False)
    fw = Folders("TEST_FW_DIR", (".crf",), "firmware file", "fw_list", default_roots=lambda: [tmp_path], default_label="CTRE Tuner X's saved files")
    assert fw.notice() == "TEST_FW_DIR is not set, so firmware files are read from CTRE Tuner X's saved files."


def test_a_non_recursive_folder_lists_only_the_files_directly_in_it(tmp_path, monkeypatch):
    (tmp_path / "app-data" / "firestore").mkdir(parents=True)
    (tmp_path / "top.crf").write_bytes(b"x")
    (tmp_path / "app-data" / "deep.crf").write_bytes(b"x")
    monkeypatch.setenv("TEST_FW_DIR", str(tmp_path))
    flat = Folders("TEST_FW_DIR", (".crf",), "firmware file", "fw_list", recursive=False)
    assert [p.name for p in flat.find()] == ["top.crf"] and flat.resolve("top.crf").name == "top.crf"
    with pytest.raises(CtreError, match="No firmware file named 'deep.crf'"):
        flat.resolve("deep.crf")  # a bare name is not searched for in sub-folders either
    assert sorted(p.name for p in Folders("TEST_FW_DIR", (".crf",), "firmware file", "fw_list").find()) == ["deep.crf", "top.crf"]
    monkeypatch.setenv("TEST_FW_DIR", str(tmp_path / "missing"))
    assert flat.find() == []  # an unreadable folder is just empty


def test_tuner_xs_caniv_is_hashed_once_while_the_file_is_unchanged(tmp_path, monkeypatch, index_file):
    from ctre_helpers import PAYLOAD

    from frc_mcp_ctre import store
    from frc_mcp_ctre.fetch import load_index
    from frc_mcp_ctre.store import installed

    cache = tmp_path / "Packages" / "CTRElectronics.PhoenixTunerXPreview_x" / "LocalCache"
    cache.mkdir(parents=True)
    (cache / "caniv.exe").write_bytes(PAYLOAD)
    monkeypatch.setenv("CTRE_TUNERX_DIR", str(tmp_path / "Packages"))
    reads = []
    original = store.hash_all
    monkeypatch.setattr(store, "hash_all", lambda path: (reads.append(path), original(path))[1])
    store._DIGESTS.clear()
    index = load_index().index
    for _ in range(3):
        assert [i.version for i in installed(["caniv"], index)["caniv"]] == ["1.1.1.0"]
    assert len(reads) == 1
    (cache / "caniv.exe").write_bytes(b"a different build")  # changed: hashed again, and no longer the file the index knows
    assert installed(["caniv"], index)["caniv"] == [] and len(reads) == 2


def test_assess_is_where_a_tools_policy_decides_what_counts_as_installed(tools_dir, index_file, monkeypatch):
    import json

    from ctre_helpers import INDEX

    from frc_mcp_ctre import choose
    from frc_mcp_ctre.index import parse_index
    from frc_mcp_ctre.store import installed

    index = parse_index(json.dumps(INDEX))
    fit = choose.Fit("2026", 19, True, "test")
    for tool, version in (("owlet", "26.0.0"), ("owlet", "26.3.0"), ("caniv", "1.1.1.0")):
        fake_install(tools_dir, tool if tool == "owlet" else "caniv", tool, version)
    have = installed(["owlet", "caniv"], index)
    reader = choose.assess(index, "owlet", have["owlet"], fit, None, "windowsx86-64")
    assert reader.current.version == "26.3.0" and [i.version for i in reader.other] == ["26.0.0"]  # level 18 is in the season but is not level 19
    standalone = choose.assess(index, "caniv", have["caniv"], fit, None, "windowsx86-64")
    assert standalone.current.version == "1.1.1.0" and standalone.other == [] and standalone.note is None
    nothing = choose.assess(index, "passerine", [], fit, None, "macosuniversal")
    assert nothing.current is None and nothing.recommended.version == "1.0.0" and "2027-alpha-7" in nothing.note


@pytest.mark.parametrize("name", ["x:y", "song.mid:stream", "CON", "nul.chrp", "COM1", "lpt9.txt", "name.", "name ", "a" + chr(92) + "b"])
def test_an_output_name_cannot_be_a_device_name_or_a_data_stream(tmp_path, name):
    with pytest.raises(CtreError, match="plain file name|not a usable file name"):
        new_output_path(tmp_path / "song.mid", name, ".chrp")

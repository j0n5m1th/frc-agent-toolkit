"""Installed tools, getting the index, downloading with hash checks, and the status report."""

import json
import os
import sys
import time

import pytest
from ctre_helpers import INDEX, MD5, PAYLOAD, fake_install, make_project, write_index

from frc_mcp_ctre import config, fetch
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.fetch import download_tool, load_index
from frc_mcp_ctre.platform import platform_key
from frc_mcp_ctre.project import read_project
from frc_mcp_ctre.runner import clean_output, run_tool
from frc_mcp_ctre.status import build_status, current_status
from frc_mcp_ctre.store import installed, target_path
from frc_mcp_ctre.versions import parse_version  # noqa: F401  (keeps the import list honest for the helpers above)

# -- installed tools -------------------------------------------------------------------------------------------------


def test_installed_tools_are_read_from_tuner_xs_layout_newest_first(tools_dir):
    fake_install(tools_dir, "owlet", "owlet", "26.2.0")
    fake_install(tools_dir, "owlet", "owlet", "26.10.0")
    fake_install(tools_dir, "phoenix-diag", "PhoenixDiagnosticsProgram", "26.2.0")
    (tools_dir / "owlet" / "26.2.0" / "owlet-26.2.0-windowsx86-64.md5").write_text("x", encoding="utf-8")
    found = installed(["owlet", "PhoenixDiagnosticsProgram", "caniv"])
    assert [i.version for i in found["owlet"]] == ["26.10.0", "26.2.0"]
    assert [i.version for i in found["PhoenixDiagnosticsProgram"]] == ["26.2.0"] and found["caniv"] == []


def test_advantagescopes_owlet_copies_are_found_with_their_compliancy_and_ctres_own_folder_wins(tools_dir, index_file, tmp_path, monkeypatch):
    scope = tmp_path / "scope" / "owlet"
    scope.mkdir(parents=True)
    for name in ("owlet-26.70.0-C21.exe", "owlet-26.3.0-C19.exe", "notes.txt"):
        (scope / name).write_bytes(PAYLOAD)
    monkeypatch.setenv("CTRE_ADVANTAGESCOPE_DIR", str(scope.parent))
    fake_install(tools_dir, "owlet", "owlet", "26.3.0")
    found = {i.version: i for i in installed(["owlet"], load_index().index)["owlet"]}  # outside copies are checked against the index's hashes
    assert set(found) == {"26.70.0", "26.3.0"}
    assert found["26.70.0"].source == "advantagescope" and found["26.70.0"].compliancy == 21
    assert found["26.3.0"].source == "ctre"


def test_a_download_is_placed_where_tuner_x_keeps_it(tools_dir):
    item = parse_index_item("owlet", "26.3.0")
    assert target_path(item, "windowsx86-64") == tools_dir / "owlet" / "26.3.0" / "owlet-26.3.0-windowsx86-64.exe"
    assert target_path(parse_index_item("PhoenixDiagnosticsProgram", "26.1.3"), "windowsx86-64").parent.parent.name == "phoenix-diag"
    with pytest.raises(CtreError, match="no build for macosuniversal"):
        target_path(item, "macosuniversal")


def parse_index_item(tool, version):
    from frc_mcp_ctre.index import parse_index

    return parse_index(json.dumps(INDEX)).find(tool, version)


# -- the index: where it comes from ----------------------------------------------------------------------------------


def serve(monkeypatch, body=None, fail=None):
    calls = []

    def fake_get(url, sink, limit, timeout):
        calls.append(url)
        if fail:
            raise CtreError(fail)
        sink.write(body if body is not None else json.dumps(INDEX).encode())

    monkeypatch.setattr(fetch, "http_get", fake_get)
    return calls


def test_the_index_file_setting_beats_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("CTRE_INDEX_FILE", str(write_index(tmp_path / "mine.json")))
    result = load_index()
    assert result.source == "file" and "owlet" in result.index.tools


def test_a_missing_index_file_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("CTRE_INDEX_FILE", str(tmp_path / "nope.json"))
    with pytest.raises(CtreError, match="cannot be read"):
        load_index()


def test_the_network_index_is_saved_and_then_reused_while_fresh(monkeypatch):
    calls = serve(monkeypatch)
    assert load_index().source == "network" and calls == [config.INDEX_URL]
    again = load_index()
    assert again.source == "cache" and len(calls) == 1
    assert load_index(refresh=True).source == "network" and len(calls) == 2


def test_an_old_saved_index_is_refreshed_and_kept_when_the_network_fails(monkeypatch):
    serve(monkeypatch)
    load_index()
    saved = config.tools_dir() / ".mcp-ctre" / "index.json"
    old = time.time() - 3 * 86400
    os.utime(saved, (old, old))
    serve(monkeypatch, fail="no route")
    result = load_index()
    assert result.source == "stale-cache" and "no route" in result.note and "3 days" in result.note


def test_with_no_network_and_nothing_saved_the_bundled_snapshot_is_used_and_says_so(monkeypatch):
    serve(monkeypatch, fail="offline")
    result = load_index()
    assert result.source == "snapshot" and "bundled" in result.note and "offline" in result.note
    assert "owlet" in result.index.tools


def test_offline_mode_never_touches_the_network():
    assert load_index(offline=True).source == "snapshot"  # the autouse fixture fails any network call


def test_a_garbled_network_answer_is_not_saved(monkeypatch):
    serve(monkeypatch, body=b"<html>captive portal</html>")
    result = load_index()
    assert result.source == "snapshot" and not (config.tools_dir() / ".mcp-ctre" / "index.json").exists()


@pytest.mark.parametrize("url", ["http://redist.ctr-electronics.com/index.json", "https://evil.example/index.json",
                                 "https://redist.ctr-electronics.com.evil.example/x", "file:///etc/passwd"])
def test_only_https_on_ctres_host_is_ever_fetched(url):
    with pytest.raises(CtreError, match="only https links"):
        fetch.check_url(url)


# -- downloading -----------------------------------------------------------------------------------------------------


def serve_file(monkeypatch, body=PAYLOAD):
    calls = []

    def fake_get(url, sink, limit, timeout):
        calls.append(url)
        sink.write(body)

    monkeypatch.setattr(fetch, "http_get", fake_get)
    return calls


def test_a_download_matching_ctres_hashes_is_kept(tools_dir, monkeypatch):
    calls = serve_file(monkeypatch)
    path = download_tool(parse_index_item("owlet", "26.3.0"), "windowsx86-64")
    assert path.read_bytes() == PAYLOAD and path.parent == tools_dir / "owlet" / "26.3.0"
    assert calls == ["https://redist.ctr-electronics.com/tools/owlet/26.3.0/owlet-26.3.0-windowsx86-64.exe"]
    assert not list(path.parent.glob("*.part"))
    if sys.platform != "win32":
        assert os.access(download_tool(parse_index_item("owlet", "26.3.0"), "linuxx86-64"), os.X_OK)


def test_a_download_that_fails_its_hash_is_deleted_and_never_installed(tools_dir, monkeypatch):
    serve_file(monkeypatch, body=b"tampered")
    with pytest.raises(CtreError, match="failed its md5/sha1 check"):
        download_tool(parse_index_item("owlet", "26.3.0"), "windowsx86-64")
    assert list((tools_dir / "owlet" / "26.3.0").iterdir()) == []


def test_an_existing_install_is_left_alone(tools_dir, monkeypatch):
    path = fake_install(tools_dir, "owlet", "owlet", "26.3.0")
    path.rename(path.with_name("owlet-26.3.0-windowsx86-64.exe"))
    calls = serve_file(monkeypatch)
    download_tool(parse_index_item("owlet", "26.3.0"), "windowsx86-64")
    assert calls == [] and path.with_name("owlet-26.3.0-windowsx86-64.exe").read_bytes() == PAYLOAD


def test_a_download_without_published_hashes_is_refused(monkeypatch):
    broken = json.loads(json.dumps(INDEX))
    urls = broken["Tools"][0]["Items"][3]["Urls"]
    for key in [k for k in urls if k.endswith(("-md5", "-sha1"))]:
        del urls[key]
    from frc_mcp_ctre.index import parse_index

    item = parse_index(json.dumps(broken)).find("owlet", "26.3.0")
    serve_file(monkeypatch)
    with pytest.raises(CtreError, match="published no hash"):
        download_tool(item, "windowsx86-64")


def test_a_link_in_the_index_that_leaves_ctres_host_is_refused_before_any_request(monkeypatch):
    bad = json.loads(json.dumps(INDEX))
    bad["Tools"][0]["Items"][3]["Urls"]["windowsx86-64"] = "https://evil.example/owlet.exe"
    from frc_mcp_ctre.index import parse_index

    calls = serve_file(monkeypatch)
    with pytest.raises(CtreError, match="only https links"):
        download_tool(parse_index(json.dumps(bad)).find("owlet", "26.3.0"), "windowsx86-64")
    assert calls == []
    assert MD5  # the hash itself was fine: the host alone stopped it


# -- the status report -----------------------------------------------------------------------------------------------


def rows(report):
    return {r["tool"]: r for r in report["tools"]}


def test_status_without_a_project_uses_the_latest_channel(tools_dir, index_file):
    fake_install(tools_dir, "owlet", "owlet", "26.1.0")
    report = current_status()
    assert report["channel"] == "2026" and "project" not in report
    owlet = rows(report)["owlet"]
    assert (owlet["use"], owlet["recommended"], owlet["status"]) == ("26.1.0", "26.3.0", "update")
    assert owlet["gaps"] == ["C19 (2026): have 26.1.0, newest 26.3.0", "C13 (2025): missing, newest 25.4.1"]  # one Owlet per released compliancy, like AdvantageScope
    assert rows(report)["caniv"]["status"] == "missing"
    assert rows(report)["passerine"]["status"] == "no_build"


def test_status_marks_a_current_tool_ok_and_sets_other_seasons_aside(tools_dir, index_file):
    fake_install(tools_dir, "owlet", "owlet", "26.3.0")
    fake_install(tools_dir, "owlet", "owlet", "26.70.0")  # a 2027 alpha tool next to a 2026 project
    owlet = rows(current_status())["owlet"]
    assert (owlet["use"], owlet["status"], owlet["other"]) == ("26.3.0", "ok", ["26.70.0"])


def test_status_for_a_project_names_its_channel_and_a_newer_phoenix(tools_dir, index_file, tmp_path, monkeypatch):
    monkeypatch.setenv("CTRE_PROJECT_DIR", str(make_project(tmp_path / "robot", "26.1.3")))
    fake_install(tools_dir, "phoenix-diag", "PhoenixDiagnosticsProgram", "26.1.3")
    report = current_status()
    assert report["project"] == {"folder": str(tmp_path / "robot"), "year": "2026", "gradlerio": "2026.2.1", "phoenix6": "26.1.3", "phoenix6_newer": "26.3.0"}
    diag = rows(report)["PhoenixDiagnosticsProgram"]
    assert (diag["use"], diag["recommended"], diag["status"]) == ("26.1.3", "26.1.3", "ok")  # a `match` tool follows the project's Phoenix


def test_status_for_one_tool_and_an_unknown_tool(index_file):
    assert [r["tool"] for r in current_status(only="OWLET")["tools"]] == ["owlet"]
    with pytest.raises(CtreError, match="no tool"):
        current_status(only="nope")


def test_status_carries_the_index_note_when_the_live_index_was_not_used(monkeypatch, tmp_path):
    serve(monkeypatch, fail="offline")
    report = build_status(load_index(), read_project(make_project(tmp_path / "r")), {}, "windowsx86-64")
    assert report["index"]["source"] == "snapshot" and "bundled" in report["index"]["note"]


def test_the_platform_can_be_forced_and_a_bad_value_is_refused(monkeypatch):
    assert platform_key() == "windowsx86-64"
    monkeypatch.setenv("CTRE_PLATFORM", "amiga")
    with pytest.raises(CtreError, match="CTRE_PLATFORM"):
        platform_key()


# -- running a tool --------------------------------------------------------------------------------------------------


def test_progress_bars_and_blank_lines_are_cleaned_out_of_tool_output():
    raw = "Extracting a.hoot...\r\nProgress: [  0%] [....]\rProgress: [ 50%] [##..]Progress: [100%] [####]\r\n\r\nOutput has been written to b\r\n"
    assert clean_output(raw) == "Extracting a.hoot...\nOutput has been written to b"


async def test_a_tool_is_run_without_a_shell_and_its_output_comes_back_cleaned():
    from pathlib import Path

    result = await run_tool(Path(sys.executable), ["-c", "print('Progress: [ 10%] [#.]  hello'); import sys; sys.stderr.write('warn\\n'); sys.exit(3)"], 30)
    assert result.returncode == 3 and sorted(result.output.splitlines()) == ["hello", "warn"] and not result.truncated


async def test_a_tool_that_runs_too_long_is_stopped_and_one_that_cannot_start_is_reported(tmp_path):
    from pathlib import Path

    with pytest.raises(CtreError, match="did not finish within 1 s"):
        await run_tool(Path(sys.executable), ["-c", "import time; time.sleep(30)"], 1)
    with pytest.raises(CtreError, match="Could not start"):
        await run_tool(tmp_path / "missing-tool", [], 5)


def test_tuner_xs_copy_of_caniv_is_found_by_its_hash_and_never_by_name(tmp_path, monkeypatch, index_file):
    packages = tmp_path / "Packages"
    cache = packages / "CTRElectronics.PhoenixTunerXPreview_x" / "LocalCache"
    cache.mkdir(parents=True)
    monkeypatch.setenv("CTRE_TUNERX_DIR", str(packages))
    index = load_index(offline=True).index
    assert installed(["caniv"], index)["caniv"] == []  # no file yet
    (cache / "caniv.exe").write_bytes(b"some other build")
    assert installed(["caniv"], index)["caniv"] == []  # not a file the index knows
    (cache / "caniv.exe").write_bytes(PAYLOAD)  # the made-up index publishes PAYLOAD's hashes for every file
    found = installed(["caniv"], index)["caniv"]
    assert [(i.version, i.source) for i in found] == [("1.1.1.0", "tunerx")]
    assert installed(["caniv"])["caniv"] == []  # without the index it cannot be identified
    assert installed(["owlet"], index)["owlet"] == []  # only caniv is taken from there


def test_a_reader_tool_counts_only_its_exact_level_as_current(tools_dir, index_file, tmp_path, monkeypatch):
    monkeypatch.setenv("CTRE_PROJECT_DIR", str(make_project(tmp_path / "robot", "26.1.3")))  # compliancy 19
    fake_install(tools_dir, "owlet", "owlet", "26.0.0")  # level 18 is in the 2026 season, but would return almost nothing for a level 19 log
    owlet = rows(current_status())["owlet"]
    assert (owlet["use"], owlet["recommended"], owlet["status"]) == (None, "26.3.0", "missing") and "other" in owlet


def test_a_changed_index_format_version_is_reported_not_refused(monkeypatch, tmp_path):
    changed = json.loads(json.dumps(INDEX))
    changed["JsonVersion"] = "2.0.0.0"
    monkeypatch.setenv("CTRE_INDEX_FILE", str(write_index(tmp_path / "new.json", changed)))
    result = load_index()
    assert "2.0.0.0" in result.note and "1.0.0.0" in result.note and "owlet" in result.index.tools


def test_a_download_refuses_to_start_when_the_disk_is_nearly_full(tools_dir, monkeypatch):
    calls = serve_file(monkeypatch)
    monkeypatch.setattr(fetch.shutil, "disk_usage", lambda path: type("U", (), {"free": 1024})())
    with pytest.raises(CtreError, match="is free where"):
        download_tool(parse_index_item("owlet", "26.3.0"), "windowsx86-64")
    assert calls == []


def test_two_downloads_of_one_file_use_different_temporary_names(tools_dir, monkeypatch):
    names = []

    def fake_get(url, sink, limit, timeout):
        names.append(sink.name)
        sink.write(PAYLOAD)

    monkeypatch.setattr(fetch, "http_get", fake_get)
    item = parse_index_item("owlet", "26.3.0")
    download_tool(item, "windowsx86-64")
    (tools_dir / "owlet" / "26.3.0" / "owlet-26.3.0-windowsx86-64.exe").unlink()
    download_tool(item, "windowsx86-64")
    assert len(set(names)) == 2 and all(n.endswith(".part") for n in names)

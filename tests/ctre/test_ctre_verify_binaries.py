"""A CTRE tool is only run when it is the file CTRE published: install records, outside copies, the hash cache and what a download does with a file already there."""

import hashlib
import json
import os

import pytest
from ctre_helpers import INDEX, PAYLOAD, fake_install

from frc_mcp_ctre import fetch, store
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.fetch import download_tool, load_index
from frc_mcp_ctre.index import parse_index
from frc_mcp_ctre.resolve import newest_installed
from frc_mcp_ctre.status import current_status
from frc_mcp_ctre.store import (
    installed,
    read_sidecar,
    scan,
    sidecar_path,
    write_sidecar,
)

SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
OTHER = b"a different program"


def item(tool, version):
    found = parse_index(json.dumps(INDEX)).find(tool, version)
    assert found is not None
    return found


def serve_file(monkeypatch, body=PAYLOAD):
    calls = []

    def fake_get(url, sink, limit, timeout):
        calls.append(url)
        sink.write(body)

    monkeypatch.setattr(fetch, "http_get", fake_get)
    return calls


def rows(report):
    return {r["tool"]: r for r in report["tools"]}


# -- the record written at install ---------------------------------------------------------------------------------------


def test_an_install_writes_a_sha256_record_after_the_hashes_matched(tools_dir, monkeypatch):
    serve_file(monkeypatch)
    path = download_tool(item("owlet", "26.3.0"), "windowsx86-64")
    assert read_sidecar(path) == SHA256 and sidecar_path(path).read_text(encoding="utf-8").startswith(SHA256)
    assert [i.version for i in installed(["owlet"])["owlet"]] == ["26.3.0"]  # the record is not mistaken for a tool, and the file passes


def test_a_download_that_fails_its_hash_leaves_no_record(tools_dir, monkeypatch):
    serve_file(monkeypatch, body=OTHER)
    with pytest.raises(CtreError, match="failed its"):
        download_tool(item("owlet", "26.3.0"), "windowsx86-64")
    assert list((tools_dir / "owlet" / "26.3.0").iterdir()) == []


def test_a_modified_install_is_not_run_and_status_says_unverified(tools_dir, index_file):
    path = fake_install(tools_dir, "caniv", "caniv", "1.1.1.0")
    assert newest_installed("caniv").version == "1.1.1.0"
    path.write_bytes(OTHER)  # replaced after it was installed
    found = scan(["caniv"], load_index().index)["caniv"]
    assert [i.verified for i in found] == [False] and "has changed since it was installed" in found[0].problem
    assert installed(["caniv"], load_index().index)["caniv"] == []
    with pytest.raises(CtreError, match="not verified.*has changed.*ctre_install tool=caniv version=1.1.1.0"):
        newest_installed("caniv")
    caniv = rows(current_status())["caniv"]
    assert caniv["status"] == "unverified" and caniv["use"] is None and "changed" in caniv["unverified"][0]


def test_a_record_for_one_file_does_not_vouch_for_a_replacement_that_has_no_record(tools_dir, index_file):
    path = fake_install(tools_dir, "caniv", "caniv", "1.1.1.0")
    sidecar_path(path).unlink()
    path.write_bytes(OTHER)
    with pytest.raises(CtreError, match="not verified.*was not installed by this server"):
        newest_installed("caniv")


def test_a_file_without_a_record_is_trusted_once_only_when_ctre_published_it(tools_dir, index_file):
    path = fake_install(tools_dir, "caniv", "caniv", "1.1.1.0")
    sidecar_path(path).unlink()  # installed by an earlier version, or copied here
    assert newest_installed("caniv").version == "1.1.1.0"
    assert read_sidecar(path) == SHA256  # remembered now
    path.write_bytes(OTHER)
    with pytest.raises(CtreError, match="has changed"):
        newest_installed("caniv")  # and held to it from then on


def test_a_file_that_ctre_never_published_is_unverified_and_does_not_run(tools_dir, index_file):
    path = fake_install(tools_dir, "caniv", "caniv", "1.1.1.0")
    sidecar_path(path).unlink()
    path.write_bytes(OTHER)
    with pytest.raises(CtreError, match="not verified.*does not match a hash CTRE published"):
        newest_installed("caniv")
    assert read_sidecar(path) is None  # nothing was recorded for it


def test_an_unverified_copy_does_not_hide_a_verified_one_of_the_same_version(tools_dir, index_file, tmp_path, monkeypatch):
    scope = tmp_path / "scope" / "owlet"
    scope.mkdir(parents=True)
    (scope / "owlet-26.3.0-C19.exe").write_bytes(PAYLOAD)  # CTRE's file, kept by AdvantageScope
    monkeypatch.setenv("CTRE_ADVANTAGESCOPE_DIR", str(scope.parent))
    path = fake_install(tools_dir, "owlet", "owlet", "26.3.0")
    path.write_bytes(OTHER)  # our own copy was damaged
    have = installed(["owlet"], load_index().index)["owlet"]
    assert [(i.version, i.source) for i in have] == [("26.3.0", "advantagescope")]


# -- copies kept by other programs -----------------------------------------------------------------------------------------


def advantagescope(tmp_path, monkeypatch, name, body):
    scope = tmp_path / "scope" / "owlet"
    scope.mkdir(parents=True)
    (scope / name).write_bytes(body)
    monkeypatch.setenv("CTRE_ADVANTAGESCOPE_DIR", str(scope.parent))


def test_an_advantagescope_copy_that_matches_the_published_hash_is_accepted(tools_dir, index_file, tmp_path, monkeypatch):
    advantagescope(tmp_path, monkeypatch, "owlet-26.3.0-C19.exe", PAYLOAD)
    found = installed(["owlet"], load_index().index)["owlet"]
    assert [(i.version, i.source, i.verified) for i in found] == [("26.3.0", "advantagescope", True)]


def test_an_advantagescope_copy_that_does_not_match_is_refused_and_reported(tools_dir, index_file, tmp_path, monkeypatch):
    advantagescope(tmp_path, monkeypatch, "owlet-26.3.0-C19.exe", OTHER)
    index = load_index().index
    assert installed(["owlet"], index)["owlet"] == []
    problem = scan(["owlet"], index)["owlet"][0].problem
    assert "AdvantageScope's copy" in problem and "only way to enable it" in problem
    owlet = rows(current_status())["owlet"]
    assert owlet["status"] == "unverified" and "advantagescope" in owlet["unverified"][0]


def test_an_advantagescope_copy_of_a_version_the_index_does_not_list_is_refused(tools_dir, index_file, tmp_path, monkeypatch):
    advantagescope(tmp_path, monkeypatch, "owlet-99.0.0-C30.exe", PAYLOAD)
    found = scan(["owlet"], load_index().index)["owlet"]
    assert [i.verified for i in found] == [False] and "does not have it" in found[0].problem
    assert scan(["owlet"])["owlet"][0].verified is False  # and with no index at all nothing outside can be vouched for


async def test_an_unverified_outside_owlet_is_not_run_and_the_error_says_how_to_get_a_real_one(tools_dir, index_file, tmp_path, monkeypatch):
    from frc_mcp_ctre_owlet import owlet

    advantagescope(tmp_path, monkeypatch, "owlet-26.3.0-C19.exe", OTHER)
    hoot = tmp_path / "match.hoot"
    hoot.write_bytes(bytes(70) + bytes([19]) + b"x")
    with pytest.raises(CtreError, match="not verified.*only way to enable it.*ctre_install tool=owlet compliancy=19"):
        await owlet.pick(hoot)


def test_tuner_xs_caniv_that_the_index_does_not_know_is_unverified(tools_dir, index_file, tmp_path, monkeypatch):
    cache = tmp_path / "Packages" / "CTRElectronics.PhoenixTunerXPreview_x" / "LocalCache"
    cache.mkdir(parents=True)
    (cache / "caniv.exe").write_bytes(OTHER)
    monkeypatch.setenv("CTRE_TUNERX_DIR", str(tmp_path / "Packages"))
    index = load_index().index
    found = scan(["caniv"], index)["caniv"]
    assert [(i.version, i.source, i.verified) for i in found] == [("unknown", "tunerx", False)]
    assert installed(["caniv"], index)["caniv"] == []
    with pytest.raises(CtreError, match="not verified.*Tuner X"):
        newest_installed("caniv")
    assert rows(current_status())["caniv"]["status"] == "unverified"


# -- the hash cache --------------------------------------------------------------------------------------------------------


def test_the_hash_is_remembered_until_the_file_changes_in_size_or_time(tmp_path, monkeypatch):
    reads = []
    original = store.hash_all
    monkeypatch.setattr(store, "hash_all", lambda path: (reads.append(path), original(path))[1])
    store._DIGESTS.clear()
    path = tmp_path / "tool.exe"
    path.write_bytes(PAYLOAD)
    for _ in range(3):
        assert store.file_digests(path)["sha256"] == SHA256
    assert len(reads) == 1
    before = path.stat()
    path.write_bytes(OTHER[: len(PAYLOAD)].ljust(len(PAYLOAD), b"!"))  # same size, different bytes
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))  # and a later modification time
    assert store.file_digests(path)["sha256"] != SHA256 and len(reads) == 2
    path.write_bytes(PAYLOAD + b"!")  # a different size
    assert store.file_digests(path)["sha256"] == hashlib.sha256(PAYLOAD + b"!").hexdigest() and len(reads) == 3
    assert len([k for k in store._DIGESTS if k[0] == str(path)]) == 1  # the old entries were dropped


# -- a download over a file that is already there ------------------------------------------------------------------------


def place(tools_dir, body, record):
    path = tools_dir / "owlet" / "26.3.0" / "owlet-26.3.0-windowsx86-64.exe"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    if record:
        write_sidecar(path, hashlib.sha256(body).hexdigest())
    return path


def test_a_matching_file_is_already_installed_and_gets_its_record(tools_dir, monkeypatch):
    path = place(tools_dir, PAYLOAD, record=False)
    calls = serve_file(monkeypatch)
    assert fetch.is_installed(item("owlet", "26.3.0"), "windowsx86-64") is True
    assert download_tool(item("owlet", "26.3.0"), "windowsx86-64") == path
    assert calls == [] and read_sidecar(path) == SHA256


def test_a_file_the_user_placed_that_differs_is_never_replaced_or_called_installed(tools_dir, monkeypatch):
    path = place(tools_dir, OTHER, record=False)
    calls = serve_file(monkeypatch)
    for call in (fetch.is_installed, download_tool):
        with pytest.raises(CtreError, match="not the file CTRE published.*did not install it, so it is not replaced"):
            call(item("owlet", "26.3.0"), "windowsx86-64")
    assert calls == [] and path.read_bytes() == OTHER and read_sidecar(path) is None


def test_a_damaged_install_is_downloaded_again(tools_dir, monkeypatch):
    path = place(tools_dir, PAYLOAD, record=True)
    path.write_bytes(OTHER)  # changed after the install
    assert fetch.is_installed(item("owlet", "26.3.0"), "windowsx86-64") is False
    calls = serve_file(monkeypatch)
    download_tool(item("owlet", "26.3.0"), "windowsx86-64")
    assert len(calls) == 1 and path.read_bytes() == PAYLOAD and read_sidecar(path) == SHA256
    assert installed(["owlet"])["owlet"][0].version == "26.3.0"


def test_a_replacement_that_fails_its_hash_leaves_the_old_file_alone(tools_dir, monkeypatch):
    path = place(tools_dir, PAYLOAD, record=True)
    path.write_bytes(OTHER)
    serve_file(monkeypatch, body=b"tampered")
    with pytest.raises(CtreError, match="failed its"):
        download_tool(item("owlet", "26.3.0"), "windowsx86-64")
    assert path.read_bytes() == OTHER

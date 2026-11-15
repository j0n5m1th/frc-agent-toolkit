"""The shared allowed-folders rule (`frc_mcp_core.folders`), and that the four servers that use it are all equally strict:
the WPILog, JFR and CTRE file servers and the Gradle project servers refuse a `..` or a link that leads out of the allowed folders."""

import os
from pathlib import Path

import pytest

from frc_mcp_core.folders import (
    WALK_DEPTH_LIMIT,
    AllowedFolders,
    inside,
    newest_first,
    walk_folders,
)
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.folders import Folders
from frc_mcp_jfr import config as jfr_config
from frc_mcp_jfr.errors import JfrError
from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilib.gradle.project import ProjectSource
from frc_mcp_wpilog import config as wpilog_config
from frc_mcp_wpilog.errors import WPILogError

FOLDERS = AllowedFolders("TEST_ALLOWED_DIRS")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    monkeypatch.delenv("TEST_ALLOWED_DIRS", raising=False)


# -- AllowedFolders ----------------------------------------------------------------------------------------------------


def test_the_roots_are_the_setting_else_the_working_directory(tmp_path, monkeypatch):
    assert FOLDERS.roots() == [Path.cwd().resolve()] and not FOLDERS.is_set()
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("TEST_ALLOWED_DIRS", f"{a}{os.pathsep}{os.pathsep}{b}")  # an empty entry between separators is ignored
    assert FOLDERS.is_set() and FOLDERS.roots() == [a.resolve(), b.resolve()]
    monkeypatch.setenv("TEST_ALLOWED_DIRS", "   ")
    assert not FOLDERS.is_set() and FOLDERS.roots() == [Path.cwd().resolve()]


def test_a_fallback_replaces_the_working_directory_only_when_the_setting_is_unset(tmp_path, monkeypatch):
    assert FOLDERS.roots(lambda: [tmp_path]) == [tmp_path.resolve()]
    assert FOLDERS.roots(lambda: []) == [Path.cwd().resolve()]  # nothing found: the working directory
    monkeypatch.setenv("TEST_ALLOWED_DIRS", str(tmp_path / "x"))
    assert FOLDERS.roots(lambda: [tmp_path]) == [(tmp_path / "x").resolve()]  # the setting always wins


def test_inside_means_the_root_itself_or_anything_below_it(tmp_path):
    root = tmp_path.resolve()
    assert inside(root, [root]) and inside(root / "a" / "b.txt", [root])
    assert not inside(root.parent / "other", [root]) and not inside(Path(str(root) + "-sibling"), [root])  # a name that merely starts the same is not inside


def test_locate_finds_a_relative_name_under_each_folder_and_an_absolute_path_as_given(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "sub").mkdir(parents=True)
    b.mkdir()
    (a / "sub" / "x.txt").write_text("1", encoding="utf-8")
    (b / "x.txt").write_text("2", encoding="utf-8")
    monkeypatch.setenv("TEST_ALLOWED_DIRS", os.pathsep.join([str(a), str(b)]))
    assert FOLDERS.locate("sub/x.txt").inside == [(a / "sub" / "x.txt").resolve()]
    assert FOLDERS.locate("x.txt").inside == [(b / "x.txt").resolve()]
    assert FOLDERS.locate(str(a / "sub" / "x.txt")).inside == [(a / "sub" / "x.txt").resolve()]
    assert FOLDERS.locate("sub").inside == []  # a folder is not a file...
    assert FOLDERS.locate("sub", directory=True).inside == [(a / "sub").resolve()]  # ...and a file is not a folder
    assert FOLDERS.locate("x.txt", directory=True).inside == []


def test_locate_judges_a_dotdot_path_after_resolving_it(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret.txt").write_text("s", encoding="utf-8")
    monkeypatch.setenv("TEST_ALLOWED_DIRS", str(allowed))
    sneaky = FOLDERS.locate("../secret.txt")
    assert sneaky.inside == [] and sneaky.outside == [(tmp_path / "secret.txt").resolve()]
    assert FOLDERS.locate(str(allowed / ".." / "secret.txt")).outside == [(tmp_path / "secret.txt").resolve()]
    assert FOLDERS.locate(str(tmp_path / "secret.txt")).outside == [(tmp_path / "secret.txt").resolve()]  # an absolute path outside


def test_locate_uses_the_search_only_for_a_relative_name_nothing_matched(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    (allowed / "deep").mkdir(parents=True)
    target = allowed / "deep" / "found.txt"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TEST_ALLOWED_DIRS", str(allowed))
    calls = []

    def search():
        calls.append(1)
        return [target]

    assert FOLDERS.locate("found.txt", search).inside == [target.resolve()]
    assert FOLDERS.locate("deep/found.txt", search).inside == [target.resolve()] and len(calls) == 1  # matched directly: no search
    assert FOLDERS.locate(str(tmp_path / "missing.txt"), search).inside == [] and len(calls) == 1  # an absolute path is never searched for


def test_locate_sorts_a_search_result_that_leads_out_into_outside(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TEST_ALLOWED_DIRS", str(allowed))
    located = FOLDERS.locate("elsewhere.txt", lambda: [allowed / "inside.txt", elsewhere])
    assert located.outside == [elsewhere.resolve()]


def test_walk_stops_at_the_depth_limit_and_skips_hidden_and_build_folders(tmp_path):
    deep = tmp_path
    for i in range(WALK_DEPTH_LIMIT + 2):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (deep / "too_deep.txt").write_text("x", encoding="utf-8")
    (tmp_path / "top.txt").write_text("x", encoding="utf-8")
    for skipped in (".git", ".hidden", "build", "node_modules"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "skipped.txt").write_text("x", encoding="utf-8")
    seen = {folder / f for folder, files in walk_folders([tmp_path]) for f in files}
    assert tmp_path / "top.txt" in seen
    assert deep / "too_deep.txt" not in seen
    assert not any(p.name == "skipped.txt" for p in seen)


def test_newest_first_orders_by_modification_time_and_drops_vanished_and_repeated_paths(tmp_path):
    old, new, gone = tmp_path / "old.txt", tmp_path / "new.txt", tmp_path / "gone.txt"
    old.write_text("o", encoding="utf-8")
    new.write_text("n", encoding="utf-8")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    assert newest_first([old, new, gone, new]) == [new, old]


# -- the four servers are equally strict --------------------------------------------------------------------------------


def _wpilog(name: str) -> Path:
    return wpilog_config.resolve_log(name)


def _jfr(name: str) -> Path:
    return jfr_config.resolve_recording(name)


def _ctre(name: str) -> Path:
    return Folders("TEST_CTRE_DIR", (".hoot",), "hoot file", "hoot_list").resolve(name)


def _gradle(name: str) -> Path:
    return ProjectSource("TEST_PROJECT_DIR", WPILibMCPError).resolve(name)


# name -> (resolve function, the environment setting it reads, the error it raises, the file suffix, whether the thing is a folder)
CASES = {
    "wpilog": (_wpilog, "WPILOG_DIR", WPILogError, ".wpilog", False),
    "jfr": (_jfr, "JFR_DIR", JfrError, ".jfr", False),
    "ctre": (_ctre, "TEST_CTRE_DIR", CtreError, ".hoot", False),
    "gradle": (_gradle, "TEST_PROJECT_DIR", WPILibMCPError, "", True),
}


def _make(path: Path, directory: bool) -> Path:
    if directory:
        path.mkdir(parents=True)
        (path / "gradlew").write_text("", encoding="utf-8")
        (path / "gradlew.bat").write_text("", encoding="utf-8")
        (path / "build.gradle").write_text("plugins { id 'edu.wpi.first.GradleRIO' }", encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return path


@pytest.fixture
def setup(request, tmp_path, monkeypatch):
    resolve, env, error, suffix, directory = CASES[request.param]
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv(env, str(allowed))
    return resolve, error, suffix, directory, allowed, tmp_path


@pytest.mark.parametrize("setup", CASES, indirect=True)
def test_a_name_inside_the_allowed_folder_is_found(setup):
    resolve, _, suffix, directory, allowed, _ = setup
    inner = _make(allowed / f"inner{suffix}", directory)
    assert resolve(str(inner)) == inner.resolve()
    assert resolve(inner.name) == inner.resolve()


@pytest.mark.parametrize("setup", CASES, indirect=True)
def test_a_path_that_climbs_out_with_dotdot_is_refused(setup):
    resolve, error, suffix, directory, allowed, tmp_path = setup
    _make(tmp_path / f"secret{suffix}", directory)
    with pytest.raises(error, match="outside the folders"):
        resolve(f"../secret{suffix}")
    with pytest.raises(error, match="outside the folders"):
        resolve(str(allowed / ".." / f"secret{suffix}"))


@pytest.mark.parametrize("setup", CASES, indirect=True)
def test_an_absolute_path_outside_the_allowed_folder_is_refused(setup):
    resolve, error, suffix, directory, allowed, tmp_path = setup
    other = _make(tmp_path / "elsewhere" / f"other{suffix}", directory)
    with pytest.raises(error, match="outside the folders"):
        resolve(str(other))


@pytest.mark.parametrize("setup", CASES, indirect=True)
def test_a_link_inside_the_allowed_folder_that_leads_out_is_refused_by_path_and_by_name(setup):
    resolve, error, suffix, directory, allowed, tmp_path = setup
    target = _make(tmp_path / "elsewhere" / f"secret{suffix}", directory)
    try:
        (allowed / f"link{suffix}").symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("symlinks are not available here")
    with pytest.raises(error, match="outside the folders"):
        resolve(f"link{suffix}")
    with pytest.raises(error, match="outside the folders"):
        resolve(str(allowed / f"link{suffix}"))


@pytest.mark.parametrize("setup", [name for name, case in CASES.items() if not case[4]], indirect=True)
def test_a_deeper_link_found_by_a_bare_name_search_is_refused_too(setup):
    resolve, error, suffix, directory, allowed, tmp_path = setup
    target = _make(tmp_path / "elsewhere" / f"secret{suffix}", directory)
    (allowed / "sub").mkdir()
    try:
        (allowed / "sub" / f"deep{suffix}").symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not available here")
    with pytest.raises(error, match="outside the folders"):
        resolve(f"deep{suffix}")


def test_entries_of_a_folder_list_are_stripped_of_spaces_and_quotes(tmp_path, monkeypatch):
    a, b = tmp_path / "My Logs", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("TEST_ALLOWED_DIRS", f'"{a}"{os.pathsep} {b} {os.pathsep}')
    assert FOLDERS.configured() == [str(a), str(b)]
    assert FOLDERS.roots() == [a.resolve(), b.resolve()]


def test_the_walk_does_not_follow_a_link_or_junction_out_of_the_root(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    (root / "ok.txt").write_text("x", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # Windows without the symlink right: a junction needs none
        import subprocess

        if os.name != "nt" or subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True).returncode:
            pytest.skip("cannot make a link here")
    seen = {f for _folder, files in walk_folders([root]) for f in files}
    assert seen == {"ok.txt"}

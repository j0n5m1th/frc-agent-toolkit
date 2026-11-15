"""Which files the log server may read: allowed folders, name resolution, size limit."""

import os

import pytest
from dslog_builder import build_session

from frc_mcp_wpilog import config
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.logfile import load


def touch(path, size=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_the_default_folder_is_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("WPILOG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config.log_roots() == [tmp_path.resolve()]


def test_several_folders_can_be_allowed(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("WPILOG_DIR", f"{a}{os.pathsep}{b}")
    assert config.log_roots() == [a.resolve(), b.resolve()]


def test_find_logs_lists_wpilog_files_newest_first_and_skips_other_files(monkeypatch, tmp_path):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    old = touch(tmp_path / "old.wpilog")
    new = touch(tmp_path / "sub" / "new.WPILOG")
    touch(tmp_path / "notes.txt")
    os.utime(old, (1, 1))
    assert config.find_logs() == [new.resolve(), old.resolve()]
    assert config.find_logs(name_contains="OLD") == [old.resolve()]


def test_resolve_finds_a_bare_name_in_a_subfolder(monkeypatch, tmp_path):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    log = touch(tmp_path / "2026" / "match1.wpilog")
    assert config.resolve_log("match1.wpilog") == log.resolve()
    assert config.resolve_log("2026/match1.wpilog") == log.resolve()


def test_resolve_refuses_other_suffixes_missing_and_ambiguous_names(monkeypatch, tmp_path):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    touch(tmp_path / "a" / "same.wpilog")
    touch(tmp_path / "b" / "same.wpilog")
    touch(tmp_path / "secret.txt")
    with pytest.raises(WPILogError, match="Only .wpilog"):
        config.resolve_log("secret.txt")
    with pytest.raises(WPILogError, match="No log named"):
        config.resolve_log("missing.wpilog")
    with pytest.raises(WPILogError, match="matches 2 logs"):
        config.resolve_log("same.wpilog")


def test_resolve_never_leaves_the_allowed_folders(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    outside = touch(tmp_path / "outside.wpilog")
    allowed.mkdir()
    monkeypatch.setenv("WPILOG_DIR", str(allowed))
    with pytest.raises(WPILogError, match="outside the folders"):
        config.resolve_log(str(outside))
    with pytest.raises(WPILogError, match="outside the folders|No log named"):
        config.resolve_log("../outside.wpilog")


def test_the_size_limit_is_enforced_and_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    touch(tmp_path / "big.wpilog", size=2 * 1024 * 1024)
    monkeypatch.setenv("WPILOG_MAX_MB", "1")
    with pytest.raises(WPILogError, match="WPILOG_MAX_MB limit"):
        config.resolve_log("big.wpilog")
    for bad in ("zero", "0", "-3", "inf"):
        monkeypatch.setenv("WPILOG_MAX_MB", bad)
        with pytest.raises(WPILogError, match="WPILOG_MAX_MB"):
            config.max_log_bytes()


def test_a_symlink_inside_the_folder_cannot_lead_to_a_file_outside_it(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    outside = touch(tmp_path / "elsewhere" / "secret.wpilog")
    allowed.mkdir()
    try:
        (allowed / "link.wpilog").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available here")
    monkeypatch.setenv("WPILOG_DIR", str(allowed))
    with pytest.raises(WPILogError, match="outside the folders"):
        config.resolve_log("link.wpilog")  # the link resolves outside the allowed folder, so it must be refused


@pytest.mark.parametrize("linked", [".dsevents", ".dslog"])
def test_a_session_file_that_is_a_link_out_of_the_folder_is_refused(monkeypatch, tmp_path, linked):
    """Naming one file of a Driver Station session also opens its sibling, so the sibling must be inside the folders too."""
    secret_dir, allowed = tmp_path / "elsewhere", tmp_path / "allowed"
    secret_dir.mkdir()
    allowed.mkdir()
    build_session(secret_dir, "s")
    target = secret_dir / f"s{linked}"
    session = allowed / "s.dslog"
    try:
        (allowed / f"s{linked}").symlink_to(target)
        other = ".dslog" if linked == ".dsevents" else ".dsevents"
        (allowed / f"s{other}").write_bytes((secret_dir / f"s{other}").read_bytes())
    except OSError:
        pytest.skip("symlinks are not available here")
    monkeypatch.setenv("WPILOG_DIR", str(allowed))
    for name in ("s.dslog", "s.dsevents", str(session)):
        with pytest.raises(WPILogError, match="outside the folders"):
            config.resolve_log(name)


def test_a_sibling_that_resolves_outside_the_folders_is_refused_without_needing_symlinks(monkeypatch, tmp_path):
    """Same rule as above, with the link stood in for by `pair`, so it also runs where symlinks are not allowed (Windows)."""
    outside_dir, allowed = tmp_path / "elsewhere", tmp_path / "allowed"
    outside_dir.mkdir()
    allowed.mkdir()
    session = build_session(allowed)
    stray = outside_dir / session.with_suffix(".dsevents").name
    stray.write_bytes(session.with_suffix(".dsevents").read_bytes())
    monkeypatch.setenv("WPILOG_DIR", str(allowed))
    monkeypatch.setattr(config.dslog, "pair", lambda p: (session, stray))
    with pytest.raises(WPILogError, match="outside the folders"):
        config.resolve_log(session.name)


def test_a_session_whose_files_are_both_inside_the_folder_still_opens(monkeypatch, tmp_path):
    session = build_session(tmp_path)
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    assert config.resolve_log(session.name) == session.resolve()
    assert config.resolve_log(session.with_suffix(".dsevents").name) == session.resolve()


def test_a_file_deleted_during_the_search_is_skipped(monkeypatch, tmp_path):
    keep = touch(tmp_path / "keep.wpilog")
    gone = touch(tmp_path / "gone.wpilog")
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    real_stat = type(gone).stat

    def flaky(self, *args, **kwargs):
        if self.name == "gone.wpilog":
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(type(gone), "stat", flaky)
    assert config.find_logs() == [keep]


def test_the_default_folder_comes_with_a_nudge_to_set_wpilog_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("WPILOG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    notice = config.default_root_notice()
    assert notice and "WPILOG_DIR is not set" in notice and str(tmp_path.resolve()) in notice
    monkeypatch.setenv("WPILOG_DIR", "  ")  # blank counts as unset
    assert config.default_root_notice()
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    assert config.default_root_notice() is None


def test_a_name_filter_matching_only_the_events_file_still_lists_the_session(tmp_path, monkeypatch):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    dslog_path = build_session(tmp_path, "Session A")
    assert config.find_logs(name_contains="dsevents") == [dslog_path.resolve()]
    assert config.find_logs(name_contains="nothing") == []


def test_upper_case_extensions_are_listed_once_by_their_real_name(tmp_path, monkeypatch):
    monkeypatch.setenv("WPILOG_DIR", str(tmp_path))
    lower = build_session(tmp_path, "S")
    upper_log, upper_events = tmp_path / "S2.DSLOG", tmp_path / "S2.DSEVENTS"
    upper_log.write_bytes(lower.read_bytes())
    upper_events.write_bytes(lower.with_suffix(".dsevents").read_bytes())
    listed = config.find_logs()
    assert sorted(p.name for p in listed) == ["S.dslog", "S2.DSLOG"]
    assert load(config.resolve_log("S2.DSLOG")).topics["/DSLog/BatteryVoltage"].unit == "V"


def test_a_refused_log_tells_a_plugin_user_to_copy_it_and_still_names_the_setting(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    outside = touch(tmp_path / "outside.wpilog")
    allowed.mkdir()
    monkeypatch.setenv("WPILOG_DIR", str(allowed))
    with pytest.raises(WPILogError) as raised:
        config.resolve_log(str(outside))
    text = str(raised.value)
    assert "Copy the file into the project folder" in text and "WPILOG_DIR" in text
    assert not text.startswith("set WPILOG_DIR") and "; set WPILOG_DIR to allow it" not in text

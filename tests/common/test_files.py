"""write_atomic: the file is replaced whole, text and bytes, and no temporary file is left behind."""

import os

import pytest

from frc_mcp_wpilib.files import write_atomic


def test_text_and_bytes_replace_the_file_and_leave_no_temporary(tmp_path):
    target = tmp_path / "data.json"
    write_atomic(target, "one")
    assert target.read_text(encoding="utf-8") == "one"
    write_atomic(target, "two \u00e9")
    assert target.read_text(encoding="utf-8") == "two \u00e9"
    write_atomic(target, b"\x00\x01")
    assert target.read_bytes() == b"\x00\x01" and sorted(p.name for p in tmp_path.iterdir()) == ["data.json"]


def test_a_failure_is_an_oserror_and_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / "data.json"
    target.write_text("keep", encoding="utf-8")

    def broken(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="denied"):
        write_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "keep" and not (tmp_path / "data.json.tmp").exists()

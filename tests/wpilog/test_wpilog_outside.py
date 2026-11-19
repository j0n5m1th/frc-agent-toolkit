"""Reading a log outside the project folder: refused unless WPILOG_ALLOW_OUTSIDE is on (the plugin sets it and asks the person first)."""

import json

import pytest
from fastmcp.exceptions import ToolError
from log_builder import build_sample_log

from frc_mcp_wpilog.tools import file_tools


@pytest.fixture
def usb(tmp_path):
    """A folder that is not under WPILOG_DIR (`log_dir` is `tmp_path`, so the log sits beside it, in a sibling folder)."""
    folder = tmp_path.parent / (tmp_path.name + "_usb")
    folder.mkdir()
    build_sample_log(folder / "match.wpilog")
    return folder


def text(result):
    return json.loads(result.content[0].text)


async def test_a_log_elsewhere_is_refused_by_default_and_the_message_says_how_to_allow_it(log_dir, usb):
    with pytest.raises(ToolError, match="WPILOG_ALLOW_OUTSIDE=1"):
        await file_tools.wpilog_open(str(usb / "match.wpilog"))


async def test_with_the_setting_on_a_log_elsewhere_opens(log_dir, usb, monkeypatch):
    monkeypatch.setenv("WPILOG_ALLOW_OUTSIDE", "1")
    out = text(await file_tools.wpilog_open(str(usb / "match.wpilog")))
    assert out["file"] == "match.wpilog" and out["enabled"]


async def test_only_log_files_open_even_with_the_setting_on(log_dir, usb, monkeypatch):
    monkeypatch.setenv("WPILOG_ALLOW_OUTSIDE", "1")
    (usb / "notes.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(ToolError, match="Only"):
        await file_tools.wpilog_open(str(usb / "notes.txt"))


async def test_listing_another_folder_needs_the_setting_and_shows_full_paths(log_dir, usb, monkeypatch):
    with pytest.raises(ToolError, match="WPILOG_ALLOW_OUTSIDE=1"):
        await file_tools.wpilog_list(folder=str(usb))
    monkeypatch.setenv("WPILOG_ALLOW_OUTSIDE", "1")
    out = text(await file_tools.wpilog_list(folder=str(usb)))
    assert out["total"] == 1 and out["logs"][0]["file"] == str(usb / "match.wpilog")
    opened = text(await file_tools.wpilog_open(out["logs"][0]["file"]))  # the listed path is exactly what wpilog_open takes
    assert opened["file"] == "match.wpilog"


async def test_a_folder_inside_the_project_needs_no_setting(log_dir):
    assert text(await file_tools.wpilog_list(folder=str(log_dir)))["total"] == 1


async def test_a_drive_root_or_the_home_folder_is_never_searched(log_dir, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("WPILOG_ALLOW_OUTSIDE", "1")
    for folder in (Path.home(), Path(Path.home().anchor)):
        with pytest.raises(ToolError, match="too broad"):
            await file_tools.wpilog_list(folder=str(folder))


async def test_a_missing_folder_is_an_error(log_dir, monkeypatch):
    monkeypatch.setenv("WPILOG_ALLOW_OUTSIDE", "1")
    with pytest.raises(ToolError, match="not a folder"):
        await file_tools.wpilog_list(folder=str(log_dir / "nope"))


async def test_a_blank_or_bad_setting_never_turns_it_on(log_dir, usb, monkeypatch):
    monkeypatch.setenv("WPILOG_ALLOW_OUTSIDE", "")
    with pytest.raises(ToolError):
        await file_tools.wpilog_open(str(usb / "match.wpilog"))
    monkeypatch.setenv("WPILOG_ALLOW_OUTSIDE", "maybe")
    with pytest.raises(ToolError):
        await file_tools.wpilog_open(str(usb / "match.wpilog"))

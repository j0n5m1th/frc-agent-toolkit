"""The three profiling tools called directly, with canned `jcmd` output and fake recordings: no JDK needed."""

import json
import os

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_jfr import config, jdk, recorder
from frc_mcp_jfr.errors import JfrError
from frc_mcp_jfr.tools import record_tools
from frc_mcp_jfr.tools.list_tools import jfr_list
from frc_mcp_jfr.tools.live_tools import jfr_live
from frc_mcp_jfr.tools.record_tools import jfr_record

HISTOGRAM = """
 num     #instances         #bytes  class name (module)
   1:          1000        1024000  [B (java.base@25)
   2:            40           1280  app.Work
"""

THREADS = """
"main" #1 prio=5 tid=0x01 nid=11 runnable
   java.lang.Thread.State: RUNNABLE
\tat app.Work.main(Work.java:12)
"""


class _Jvm:
    def __init__(self, pid, command):
        self.pid = pid
        self.command = command


async def test_the_recording_list_is_newest_first_and_lists_a_running_one_once(rec_dir):
    recorder._pending.clear()
    (rec_dir / "older.jfr").write_bytes(b"1")
    (rec_dir / "newer.jfr").write_bytes(b"22")
    os.utime(rec_dir / "older.jfr", (1_700_000_000, 1_700_000_000))  # distinct times: the list is newest first
    os.utime(rec_dir / "newer.jfr", (1_700_000_060, 1_700_000_060))
    out = json.loads((await jfr_list()).content[0].text)
    assert [r["file"] for r in out["recordings"]] == ["newer.jfr", "older.jfr"] and out["total"] == 2
    assert out["recordings"][1]["mb"] == 0.0 and "modified" in out["recordings"][0]
    assert "hint" not in out  # JFR_DIR is set, so nothing to explain
    recorder.start(rec_dir / "older.jfr", pid=7, seconds=10)
    once = json.loads((await jfr_list()).content[0].text)
    assert [r["file"] for r in once["recordings"]] == ["older.jfr", "newer.jfr"]  # in progress first, and not twice
    assert 5 <= once["recordings"][0]["recording_s_left"] <= 10


async def test_an_unset_recordings_folder_is_explained_once(rec_dir, monkeypatch, tmp_path):
    recorder._pending.clear()
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv("JFR_DIR")
    monkeypatch.chdir(empty)  # with no JFR_DIR the server reads the folder it started in
    out = json.loads((await jfr_list("recordings", None, 5)).content[0].text)
    assert out["recordings"] == [] and "JFR_DIR is not set" in out["hint"] and "project folder" in out["hint"]


async def test_the_jvm_list_keeps_the_command_line_short(rec_dir, monkeypatch):
    async def fake_list(contains):
        assert contains == "Work"
        return [_Jvm(1234, "java Work.java " + "y" * 300), _Jvm(1235, "java Other.java")]

    monkeypatch.setattr(jdk, "list_jvms", fake_list)
    out = json.loads((await jfr_list("jvms", "Work", 1)).content[0].text)
    assert out["total"] == 2 and len(out["jvms"]) == 1
    assert out["jvms"][0]["pid"] == 1234 and len(out["jvms"][0]["command"]) == 160


async def test_live_threads_come_from_the_jcmd_text(rec_dir, monkeypatch):
    seen = []

    async def jcmd_for(pid):
        assert pid == 7
        return "C:/jdk/bin/jcmd"

    async def run(exe, args, tool_path=None):
        seen.append((exe, args, tool_path))
        return THREADS

    monkeypatch.setattr(jdk, "jcmd_for", jcmd_for)
    monkeypatch.setattr(jdk, "run", run)
    out = json.loads((await jfr_live(7, "threads", 1, 3)).content[0].text)
    assert out["pid"] == 7 and out["threads"] == 1 and out["deadlock"] is False
    assert out["rows"][0]["name"] == "main" and out["rows"][0]["state"] == "RUNNABLE"
    assert seen == [("jcmd", ["7", "Thread.print"], "C:/jdk/bin/jcmd")]


async def test_heap_growth_compares_two_snapshots_and_says_when_either_is_empty(rec_dir, monkeypatch):
    calls = []

    async def jcmd_for(pid):
        return "jcmd"

    async def run(exe, args, tool_path=None):
        calls.append(args)
        return HISTOGRAM if len(calls) == 1 else HISTOGRAM.replace("1000        1024000", "3000        3072000")

    monkeypatch.setattr(jdk, "jcmd_for", jcmd_for)
    monkeypatch.setattr(jdk, "run", run)
    out = json.loads((await jfr_live(7, "heap_growth", 1, 5)).content[0].text)
    assert out["seconds"] == 1 and out["rows"][0]["class"] == "byte[]" and out["growth_kb"] == 2000
    assert [tuple(c) for c in calls] == [("7", "GC.class_histogram"), ("7", "GC.class_histogram")]

    async def no_output(exe, args, tool_path=None):
        return "num #instances #bytes"

    monkeypatch.setattr(jdk, "run", no_output)
    with pytest.raises(ToolError, match="no class histogram for this JVM"):
        await jfr_live(7, "heap_growth", 1, 5)

    seen: list[list[str]] = []

    async def second_is_empty(exe, args, tool_path=None):
        seen.append(args)
        return HISTOGRAM if len(seen) == 1 else ""

    monkeypatch.setattr(jdk, "run", second_is_empty)
    with pytest.raises(ToolError, match="on the second snapshot"):
        await jfr_live(7, "heap_growth", 1, 5)


@pytest.mark.slow
async def test_a_recording_is_written_and_named_as_asked(rec_dir, monkeypatch):
    async def jcmd_for(pid):
        return "jcmd"

    async def run(exe, args, tool_path=None):
        assert exe == "jcmd" and args[0] == "7" and args[1] == "JFR.start"
        assert "settings=profile" in args and "duration=2s" in args and any(a.startswith("maxsize=") for a in args)
        name = next(a for a in args if a.startswith("filename=")).split("=", 1)[1]
        (rec_dir / (name.rsplit("/", 1)[-1])).write_bytes(b"0123456789")
        return ""

    monkeypatch.setattr(jdk, "jcmd_for", jcmd_for)
    monkeypatch.setattr(jdk, "run", run)
    out = json.loads((await jfr_record(7, 2, "profile", "attach", True)).content[0].text)
    assert out["pid"] == 7 and out["seconds"] == 2 and out["hint"] == "Next: jfr_summary."
    assert out["file"].startswith("attach-") and (rec_dir / out["file"]).stat().st_size == 10
    assert out["mb"] == 0.0


async def test_not_waiting_returns_at_once_and_the_recording_is_listed_as_in_progress(rec_dir, monkeypatch):
    recorder._pending.clear()

    async def jcmd_for(pid):
        return "jcmd"

    async def run(exe, args, tool_path=None):
        return ""

    monkeypatch.setattr(jdk, "jcmd_for", jcmd_for)
    monkeypatch.setattr(jdk, "run", run)
    started = json.loads((await jfr_record(7, 3, "default", "later", False)).content[0].text)
    assert started["recording_s"] == 3 and started["file"].startswith("later-")
    assert [r["file"] for r in json.loads((await jfr_list()).content[0].text)["recordings"]] == [started["file"]]
    with pytest.raises(JfrError, match="still being recorded"):
        recorder.check_ready(started["file"])


async def test_a_late_file_is_an_error_that_names_the_recording(rec_dir, monkeypatch):
    async def jcmd_for(pid):
        return "jcmd"

    async def run(exe, args, tool_path=None):  # writes nothing, like a JVM that died
        return ""

    monkeypatch.setattr(jdk, "jcmd_for", jcmd_for)
    monkeypatch.setattr(jdk, "run", run)
    monkeypatch.setattr(record_tools, "_FILE_WAIT_S", 0.1)
    with pytest.raises(ToolError, match=r"did not appear at .* within .* s.*Is JVM 7 still running"):
        await jfr_record(7, 1, "profile", None, True)


async def test_the_length_label_and_folder_are_checked_before_anything_runs(rec_dir, monkeypatch):
    async def jcmd_for(pid):
        return "jcmd"

    monkeypatch.setattr(jdk, "jcmd_for", jcmd_for)
    monkeypatch.setenv("JFR_MAX_SECONDS", "5")
    with pytest.raises(ToolError, match=r"JFR_MAX_SECONDS \(5\)"):
        await jfr_record(7, 30)
    with pytest.raises(ToolError, match="label may use only"):
        await jfr_record(7, 1, label="../x")
    monkeypatch.setattr(config, "roots", lambda: [rec_dir.parent / "rec dir"])
    with pytest.raises(ToolError, match="may not contain spaces"):
        await jfr_record(7, 1, label="spaced")

"""The profiling tools end to end against real recordings from a real JVM (skipped when no JDK 17+ is installed)."""

import asyncio
import json

import pytest
from fastmcp.exceptions import ToolError
from jfr_helpers import java_home_tool, requires_jdk

from frc_mcp_jfr.errors import JfrError

pytestmark = requires_jdk


async def call(client, name, args=None):
    result = await client.call_tool(name, args or {})
    return json.loads(result.content[0].text)


async def test_summary_lists_the_event_types_of_a_real_recording(client, make_recording):
    name = make_recording()
    out = await call(client, "jfr_summary", {"file": name})
    assert out["file"] == name and out["seconds"] and out["start_utc"] and out["events"][0]["n"] > 0
    assert (await call(client, "jfr_list"))["recordings"][0]["file"] == name


async def test_hotspots_group_real_gc_events_and_point_at_the_worst_one(client, make_recording):
    name = make_recording()
    out = await call(client, "jfr_hotspots", {"file": name, "event": "jdk.GarbageCollection", "by": "cause"})
    assert out["rows"] and out["rows"][0]["n"] >= 1 and "max_at_ms" in out["rows"][0]
    rows = {r["key"]: r for r in out["rows"]}
    assert any("System.gc" in key for key in rows)  # the program calls System.gc() on purpose
    near = await call(client, "jfr_around", {"file": name, "at_ms": out["rows"][0]["max_at_ms"], "window_ms": 50})
    assert any(e["type"] == "GarbageCollection" for e in near["events"])


async def test_an_unknown_event_names_the_types_the_recording_has(client, make_recording):
    name = make_recording()
    with pytest.raises(ToolError, match="no my.Nothing events.*Most common types"):
        await call(client, "jfr_hotspots", {"file": name, "event": "my.Nothing", "by": None})


async def test_memory_views_read_gc_allocation_and_leak_data(client, make_recording):
    name = make_recording()
    gc = await call(client, "jfr_memory", {"file": name, "view": "gc"})
    assert gc["gcs"] >= 1 and gc["pause_max_ms"] >= 0 and "heap_after_gc_mb" in gc
    alloc = await call(client, "jfr_memory", {"file": name, "view": "alloc"})
    assert alloc["total_mb"] > 0 and alloc["rows"]
    stacks = await call(client, "jfr_memory", {"file": name, "view": "alloc", "match": "byte[]"})
    assert stacks["stacks"] and any("Work" in f for s in stacks["stacks"] for f in s["frames"])
    leaks = await call(client, "jfr_memory", {"file": name, "view": "leaks"})
    assert "old_objects" in leaks and "gcs" in leaks


async def test_comparing_a_recording_with_itself_shows_no_net_change(client, make_recording):
    name = make_recording()
    out = await call(client, "jfr_compare", {"before": name, "after": name, "event": "jdk.GarbageCollection", "by": "cause"})
    assert out["net_ms"] == 0 and out["rows"]


async def test_a_running_jvm_can_be_found_and_recorded(client, running_jvm, rec_dir):
    jvms = await call(client, "jfr_list", {"kind": "jvms", "name_contains": "Work.java"})
    assert [j["pid"] for j in jvms["jvms"]] == [running_jvm.pid]
    out = await call(client, "jfr_record", {"pid": running_jvm.pid, "seconds": 2, "label": "attach"})
    assert out["file"].startswith("attach-") and (rec_dir / out["file"]).stat().st_size > 0
    summary = await call(client, "jfr_summary", {"file": out["file"]})
    assert summary["events"]
    with pytest.raises(ToolError, match="No running JVM has pid"):
        await call(client, "jfr_record", {"pid": 999999, "seconds": 1})
    with pytest.raises(ToolError, match="label may use only"):
        await call(client, "jfr_record", {"pid": running_jvm.pid, "seconds": 1, "label": "../x"})


async def test_a_program_is_recorded_with_its_own_jdk(running_jvm):
    import os
    import shutil

    from frc_mcp_jfr import jdk

    found = await jdk.target_jcmd(running_jvm.pid)
    assert found and os.path.samefile(found, java_home_tool("jcmd"))
    assert shutil.which("jcmd", path=os.path.dirname(found)) == found
    assert await jdk.target_jcmd(999999) is None


async def test_a_big_question_is_refused_and_stacks_shrink_to_fit(client, make_recording, monkeypatch):
    name = make_recording()
    monkeypatch.setenv("JFR_MAX_OUTPUT_MB", "1")
    with pytest.raises(ToolError, match="would make about .* MB of JSON.*JFR_MAX_OUTPUT_MB"):
        await call(client, "jfr_hotspots", {"file": name, "event": "jdk.ObjectAllocationSample", "by": None})  # a stack frame per sample
    ok = await call(client, "jfr_hotspots", {"file": name, "event": "jdk.GarbageCollection", "by": "cause"})  # few events: unaffected
    assert ok["rows"]
    monkeypatch.setenv("JFR_MAX_OUTPUT_MB", "12")
    shrunk = await call(client, "jfr_memory", {"file": name, "view": "alloc", "match": "byte[]"})
    assert 2 <= shrunk["stack_depth"] < 8 and shrunk["stacks"]


async def test_call_paths_callers_and_cumulative_time_from_a_real_recording(client, make_recording):
    name = make_recording()
    paths = await call(client, "jfr_hotspots", {"file": name, "event": "jdk.ObjectAllocationSample", "by": "stack", "match": "Work.main", "sort": "count"})
    assert paths["rows"] and all(r["key"].startswith("Work.main") for r in paths["rows"])
    cumulative = await call(client, "jfr_hotspots", {"file": name, "event": "jdk.ObjectAllocationSample", "by": "cumulative", "sort": "count"})
    assert any(r["key"] == "Work.main" for r in cumulative["rows"])


async def test_a_recording_can_be_started_without_waiting(client, running_jvm):
    started = await call(client, "jfr_record", {"pid": running_jvm.pid, "seconds": 3, "label": "later", "wait": False})
    assert started["recording_s"] == 3
    listed = await call(client, "jfr_list")
    assert listed["recordings"][0]["file"] == started["file"] and "recording_s_left" in listed["recordings"][0]
    with pytest.raises(ToolError, match="still being recorded"):
        await call(client, "jfr_summary", {"file": started["file"]})
    await asyncio.sleep(9)
    assert (await call(client, "jfr_summary", {"file": started["file"]}))["events"]


async def test_compare_defaults_to_the_two_newest_recordings(client, make_recording):
    first = make_recording("one.jfr", run_ms=1500)
    await asyncio.sleep(1.1)  # distinct modification times
    second = make_recording("two.jfr", run_ms=1500)
    out = await call(client, "jfr_compare", {"event": "jdk.GarbageCollection", "by": "cause"})
    assert (out["before"], out["after"]) == (first, second)
    with pytest.raises(ToolError, match="both before and after, or neither"):
        await call(client, "jfr_compare", {"event": "jdk.GarbageCollection", "before": first, "by": None})


async def test_live_threads_and_heap_growth_of_a_running_jvm(client, running_jvm):
    threads = await call(client, "jfr_live", {"pid": running_jvm.pid, "what": "threads"})
    assert threads["threads"] >= 1 and threads["deadlock"] is False and any(r["name"] == "main" for r in threads["rows"])
    growth = await call(client, "jfr_live", {"pid": running_jvm.pid, "what": "heap_growth", "seconds": 1})
    assert growth["live_mb_before"] > 0 and "rows" in growth
    with pytest.raises(ToolError, match="No running JVM has pid"):
        await call(client, "jfr_live", {"pid": 999999})


async def test_the_core_toolset_hides_jfr_live_and_restores_it():
    from frc_mcp_jfr import app

    try:
        assert app.apply_toolset("core") == "core"
        assert "jfr_live" not in {t.name for t in await app.mcp.list_tools()}
    finally:
        assert app.apply_toolset("full") == "full"
    assert "jfr_live" in {t.name for t in await app.mcp.list_tools()}
    with pytest.raises(JfrError, match="JFR_TOOLSET"):
        app.apply_toolset("everything")


async def test_the_recording_length_is_capped(client, running_jvm, monkeypatch):
    monkeypatch.setenv("JFR_MAX_SECONDS", "5")
    with pytest.raises(ToolError, match="JFR_MAX_SECONDS"):
        await call(client, "jfr_record", {"pid": running_jvm.pid, "seconds": 30})

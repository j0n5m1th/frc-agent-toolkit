"""Recording is behind JFR_ALLOW_RECORD, and the events that hold secrets (environment, properties, JVM arguments) are never recorded or read."""

import json

import pytest
from fastmcp.exceptions import ToolError
from jfr_helpers import requires_jdk

from frc_mcp_jfr import jdk
from frc_mcp_jfr.errors import JfrError
from frc_mcp_jfr.tools.record_tools import jfr_record


def _fake_jcmd(monkeypatch, seen):
    async def jcmd_for(pid):
        seen.append("jcmd_for")
        return "jcmd"

    async def run(exe, args, tool_path=None):
        seen.append(args)
        return ""

    monkeypatch.setattr(jdk, "jcmd_for", jcmd_for)
    monkeypatch.setattr(jdk, "run", run)


@pytest.mark.parametrize("value", [None, "", "0", "off"])
async def test_recording_is_refused_unless_the_switch_is_on(rec_dir, monkeypatch, value):
    seen = []
    _fake_jcmd(monkeypatch, seen)
    monkeypatch.delenv("JFR_ALLOW_RECORD")
    if value is not None:
        monkeypatch.setenv("JFR_ALLOW_RECORD", value)
    with pytest.raises(ToolError, match="JFR_ALLOW_RECORD=1"):
        await jfr_record(7, 1, wait=False)
    assert seen == []  # nothing was attached to


async def test_a_bad_switch_value_is_an_error(rec_dir, monkeypatch):
    monkeypatch.setenv("JFR_ALLOW_RECORD", "maybe")
    with pytest.raises(ToolError, match="JFR_ALLOW_RECORD must be 1 or 0"):
        await jfr_record(7, 1, wait=False)


async def test_listing_needs_no_switch(rec_dir, monkeypatch):
    from frc_mcp_jfr.tools.list_tools import jfr_list

    monkeypatch.delenv("JFR_ALLOW_RECORD")
    assert json.loads((await jfr_list()).content[0].text)["recordings"] == []


async def test_the_recording_turns_off_the_environment_and_property_events(rec_dir, monkeypatch):
    seen = []
    _fake_jcmd(monkeypatch, seen)
    await jfr_record(7, 1, wait=False)
    args = next(a for a in seen if isinstance(a, list))
    assert args[:2] == ["7", "JFR.start"]
    assert "jdk.InitialEnvironmentVariable#enabled=false" in args and "jdk.InitialSystemProperty#enabled=false" in args


@pytest.mark.parametrize("event", jdk.SECRET_EVENTS)
async def test_secret_events_are_never_read(monkeypatch, tmp_path, event):
    async def no_jfr(*args, **kwargs):
        raise AssertionError("jfr must not run")

    monkeypatch.setattr(jdk, "run", no_jfr)
    with pytest.raises(JfrError, match="never read"):
        await jdk.events(tmp_path / "x.jfr", ["jdk.ExecutionSample", event])


async def test_the_jvm_arguments_field_is_dropped_and_other_events_are_kept(monkeypatch, tmp_path):
    text = json.dumps({"recording": {"events": [
        {"type": "jdk.JVMInformation", "values": {"jvmName": "OpenJDK", "jvmArguments": "-Dtoken=hunter2"}},
        {"type": "jdk.GarbageCollection", "values": {"name": "G1", "gcId": 1}}]}})

    async def fake_run(tool, args, **kwargs):
        return text

    monkeypatch.setattr(jdk, "run", fake_run)
    found = await jdk.events(tmp_path / "x.jfr", ["jdk.JVMInformation", "jdk.GarbageCollection"])
    assert found[0] == {"type": "jdk.JVMInformation", "jvmName": "OpenJDK"} and "hunter2" not in json.dumps(found)
    assert found[1]["name"] == "G1"


async def test_the_java_arguments_field_and_process_events_are_dropped_or_refused(monkeypatch, tmp_path):
    text = json.dumps({"recording": {"events": [
        {"type": "jdk.JVMInformation", "values": {"jvmName": "OpenJDK", "javaArguments": "Main --token=hunter2", "jvmArguments": "-Dk=hunter2"}}]}})

    async def fake_run(tool, args, **kwargs):
        return text

    monkeypatch.setattr(jdk, "run", fake_run)
    found = await jdk.events(tmp_path / "x.jfr", ["jdk.JVMInformation"])
    assert found == [{"type": "jdk.JVMInformation", "jvmName": "OpenJDK"}]
    with pytest.raises(JfrError, match="never read"):
        await jdk.events(tmp_path / "x.jfr", ["jdk.SystemProcess"])
    assert "jdk.SystemProcess#enabled=false" in jdk.NO_SECRET_EVENTS


async def test_the_jvm_list_never_shows_program_arguments(monkeypatch):
    async def fake_run(tool, args, **kwargs):
        return "\n".join(["7 app.Main --token=hunter2 --password x", "8 /x/app.jar --secret=1",
                          "9 jdk.compiler/com.sun.tools.javac.launcher.Main Work.java --key=k", ""])

    monkeypatch.setattr(jdk, "run", fake_run)
    shown = [j.command for j in await jdk.list_jvms()]
    assert shown == ["app.Main ...", "/x/app.jar ...", "jdk.compiler/com.sun.tools.javac.launcher.Main Work.java ..."]
    assert "hunter2" not in " ".join(shown)
    assert await jdk.list_jvms("hunter2") == []  # a filter cannot probe the hidden arguments


@requires_jdk
async def test_a_real_recording_cannot_be_asked_for_its_environment(client, make_recording):
    name = make_recording()
    for event in jdk.SECRET_EVENTS:
        with pytest.raises(ToolError, match="never read"):
            await client.call_tool("jfr_hotspots", {"file": name, "event": event, "by": "key"})
    with pytest.raises(ToolError, match="no field 'jvmArguments'"):
        await client.call_tool("jfr_hotspots", {"file": name, "event": "jdk.JVMInformation", "by": "jvmArguments"})
    ok = await client.call_tool("jfr_hotspots", {"file": name, "event": "jdk.JVMInformation", "by": "jvmName"})
    assert "OpenJDK" in ok.content[0].text or "Java" in ok.content[0].text


@requires_jdk
async def test_a_recording_made_by_the_server_holds_no_environment_events(client, running_jvm, rec_dir):
    out = json.loads((await client.call_tool("jfr_record", {"pid": running_jvm.pid, "seconds": 2, "label": "safe"})).content[0].text)
    summary = json.loads((await client.call_tool("jfr_summary", {"file": out["file"], "top": 50})).content[0].text)
    types = {e["type"] for e in summary["events"]}
    assert types and not types & set(jdk.SECRET_EVENTS)


DENIED = {
    # event -> the secret-bearing field a newer JDK or a program could fill
    "jdk.JavaAgent": "options", "jdk.NativeAgent": "options", "jdk.ProcessStart": "command", "jdk.InitialSecurityProperty": "value",
    "jdk.SecurityPropertyModification": "value", "jdk.StringFlag": "value", "jdk.SystemProcess": "commandLine",
    "jdk.InitialSystemProperty": "value", "jdk.InitialEnvironmentVariable": "value",
}


@pytest.mark.parametrize(("event", "field"), DENIED.items())
async def test_by_a_secret_field_of_a_secret_event_returns_nothing_and_never_runs_jfr(monkeypatch, tmp_path, event, field):
    async def no_jfr(*args, **kwargs):
        raise AssertionError("jfr must not run")

    monkeypatch.setattr(jdk, "run", no_jfr)
    with pytest.raises(JfrError, match="not one of the event types|never read"):  # `by=<field>` is never reached: the event is refused first
        await jdk.events(tmp_path / "x.jfr", [event])


@pytest.mark.parametrize("field", sorted(jdk.SECRET_FIELDS))
async def test_a_secret_named_field_is_dropped_from_every_jdk_event_so_by_cannot_group_on_it(monkeypatch, tmp_path, field):
    from frc_mcp_jfr import analysis

    text = json.dumps({"recording": {"events": [
        {"type": "jdk.ExecutionSample", "values": {"startTime": "2026-01-01T00:00:00Z", field: "-Dtoken=hunter2", "state": "RUNNABLE"}}]}})

    async def fake_run(tool, args, **kwargs):
        return text

    monkeypatch.setattr(jdk, "run", fake_run)
    found = await jdk.events(tmp_path / "x.jfr", ["jdk.ExecutionSample"])
    assert field not in found[0] and "hunter2" not in json.dumps(found)
    with pytest.raises(JfrError, match="has no field"):
        analysis.hotspots(found, field, None, 5, "count")


async def test_a_jdk_event_nobody_looked_at_is_refused_and_a_programs_own_event_is_read(monkeypatch, tmp_path):
    text = json.dumps({"recording": {"events": [{"type": "team.Shot", "values": {"command": "kept: the team's own event"}}]}})

    async def fake_run(tool, args, **kwargs):
        return text

    monkeypatch.setattr(jdk, "run", fake_run)
    with pytest.raises(JfrError, match="not one of the event types"):
        await jdk.events(tmp_path / "x.jfr", ["jdk.SomeEventAFutureJdkAdds"])
    assert (await jdk.events(tmp_path / "x.jfr", ["team.Shot"]))[0]["command"].startswith("kept")


def test_every_event_the_tools_read_themselves_is_on_the_allow_list():
    from frc_mcp_jfr import analysis

    for name in ("jdk.GarbageCollection", "jdk.GCHeapSummary", "jdk.OldObjectSample", "jdk.ObjectAllocationSample", "jdk.ExecutionSample", *analysis.NEAR_EVENTS):
        jdk.refuse_secret_events([name])
    assert not set(jdk.SECRET_EVENTS) & jdk.SAFE_EVENTS

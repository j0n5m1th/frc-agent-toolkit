"""Where recordings may be read, size-bounded replies, and the JVM listing (no JDK needed)."""

import json
from pathlib import Path

import pytest

from frc_mcp_jfr import config, jdk, support
from frc_mcp_jfr.errors import JfrError


def test_only_jfr_files_inside_the_allowed_folder_can_be_read(tmp_path, monkeypatch):
    inside, outside = tmp_path / "rec", tmp_path / "other"
    inside.mkdir()
    outside.mkdir()
    (inside / "a.jfr").write_bytes(b"x")
    (outside / "b.jfr").write_bytes(b"x")
    monkeypatch.setenv("JFR_DIR", str(inside))
    assert config.resolve_recording("a.jfr") == (inside / "a.jfr").resolve()
    with pytest.raises(JfrError, match="outside the folders"):
        config.resolve_recording(str(outside / "b.jfr"))
    with pytest.raises(JfrError, match="No recording named"):
        config.resolve_recording("missing.jfr")
    with pytest.raises(JfrError, match="Only .jfr"):
        config.resolve_recording("a.txt")


def test_a_big_recording_is_refused_until_the_limit_is_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("JFR_DIR", str(tmp_path))
    (tmp_path / "big.jfr").write_bytes(b"x" * 2**21)
    monkeypatch.setenv("JFR_MAX_MB", "1")
    with pytest.raises(JfrError, match="JFR_MAX_MB"):
        config.resolve_recording("big.jfr")
    monkeypatch.setenv("JFR_MAX_MB", "4")
    assert config.resolve_recording("big.jfr").name == "big.jfr"


def test_settings_are_validated(monkeypatch):
    monkeypatch.setenv("JFR_MAX_SECONDS", "abc")
    with pytest.raises(JfrError, match="JFR_MAX_SECONDS"):
        config.max_seconds()
    monkeypatch.setenv("JFR_JAVA_HOME", "/nonexistent")
    monkeypatch.setenv("JAVA_HOME", "/nonexistent")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(JfrError, match="JDK tool `jfr`"):
        config.jdk_tool("jfr")


def test_a_long_result_loses_rows_not_validity(monkeypatch):
    monkeypatch.setenv("JFR_MAX_CHARS", "600")
    data = {"file": "a.jfr", "rows": [{"key": f"row-{i}", "n": i} for i in range(200)], "total_ms": 5}
    result = json.loads(support.reply(data).content[0].text)
    assert result["truncated"] is True and 0 < len(result["rows"]) < 200 and result["omitted_items"] + len(result["rows"]) == 200
    assert result["total_ms"] == 5  # the figures about everything stay


def test_a_question_whose_json_would_be_too_big_is_refused_before_jfr_runs(monkeypatch):
    monkeypatch.setenv("JFR_MAX_OUTPUT_MB", "10")
    s = jdk.Summary(None, 20.0, {"my.Event": 50_000, "jdk.GarbageCollection": 12})
    with pytest.raises(JfrError, match=r"my.Event: 50,000 events would make about 33 MB of JSON, over the 10 MB limit \(JFR_MAX_OUTPUT_MB\)"):
        jdk.check_output_size(s, ["my.Event"], 0)
    jdk.check_output_size(s, ["jdk.GarbageCollection"], 8)  # few events: fine even with deep stacks
    with pytest.raises(JfrError, match="over the 10 MB limit"):
        jdk.check_output_size(s, ["my.Event"], 1)  # the same events with a stack frame each are bigger still


def test_call_stacks_keep_as_many_frames_as_fit(monkeypatch):
    monkeypatch.setenv("JFR_MAX_OUTPUT_MB", "100")
    assert jdk.fit_depth(1_000, 8, floor=2) == 8  # ~24 MB
    shrunk = jdk.fit_depth(10_000, 8, floor=2)
    assert 2 <= shrunk < 8 and jdk.json_bytes(10_000, shrunk) <= 100 * 2**20
    assert jdk.fit_depth(10_000_000, 8, floor=2) == 2  # nothing fits: the floor, and the size check then refuses it


def test_parsed_events_keep_only_the_names_of_threads_classes_and_methods():
    text = json.dumps({"recording": {"events": [{"type": "jdk.ObjectAllocationSample", "values": {
        "eventThread": {"osName": "main", "osThreadId": 7, "javaName": "main", "javaThreadId": 3, "group": {"parent": None, "name": "main"}},
        "objectClass": {"classLoader": {"name": "app"}, "name": "byte[]", "package": {"name": "x"}, "modifiers": 1},
        "stackTrace": {"frames": [{"method": {"type": {"name": "a/B", "modifiers": 1}, "name": "run", "descriptor": "()V"}, "lineNumber": 4}]},
        "weight": 5}}]}})
    values = json.loads(text, object_hook=jdk.slim)["recording"]["events"][0]["values"]
    assert values["eventThread"] == "main" and values["objectClass"] == "byte[]" and values["weight"] == 5
    assert values["stackTrace"]["frames"] == [{"method": "a.B.run", "lineNumber": 4}]


async def test_a_failing_recording_read_says_which_jdk_to_try(monkeypatch):
    async def failing_run(tool, args, **kwargs):
        raise JfrError("`jfr` failed: bad file.")

    monkeypatch.setattr(jdk, "run", failing_run)
    with pytest.raises(JfrError, match="`jfr` failed: bad file.*JFR_JAVA_HOME"):
        await jdk.events(Path("x.jfr"), ["a.B"])
    with pytest.raises(JfrError) as other:  # a jcmd failure is not about recordings: no such advice
        await failing_run("jcmd", [])
    assert "JFR_JAVA_HOME" not in str(other.value)


async def test_the_jvm_list_skips_jcmd_itself_and_filters(monkeypatch):
    async def fake_run(tool, args, **kwargs):
        return "101 first.robot.Main arg\n202 org.gradle.launcher.daemon\n303 jdk.jcmd/sun.tools.jcmd.JCmd -l\nnoise\n"

    monkeypatch.setattr(jdk, "run", fake_run)
    assert [j.pid for j in await jdk.list_jvms()] == [101, 202]
    assert [j.pid for j in await jdk.list_jvms("ROBOT")] == [101]
    assert [j.command for j in await jdk.list_jvms()] == ["first.robot.Main ...", "org.gradle.launcher.daemon"]

"""Exercise evaluation reporting without starting a paid agent."""

import contextlib
import json
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import eval_output
import ntcore
import pytest
import run_with_claude_code as runner
import runner_support as support
from nt_helpers import wait_for_tool
from runner_support import FAKE_WRITE_PREFIXES, safety_environment


def test_evaluation_safety_environment_overrides_inherited_settings():
    assert safety_environment(None) == {
        "NT4_ALLOW_WRITES": "0",
        "NT4_READ_ONLY": "1",
        "NT4_WRITE_PREFIXES": "",
    }
    assert safety_environment(FAKE_WRITE_PREFIXES) == {
        "NT4_ALLOW_WRITES": "1",
        "NT4_READ_ONLY": "0",
        "NT4_WRITE_PREFIXES": FAKE_WRITE_PREFIXES,
    }


def test_stream_output_counts_unique_mcp_tool_calls_and_preserves_usage():
    tool = {"type": "tool_use", "name": "mcp__nt4__nt4_watch", "id": "tool1"}
    assistant = {"type": "assistant", "message": {"content": [tool]}}
    result = {"type": "result", "result": "<response>4</response>", "usage": {"input_tokens": 15}}
    output = "\n".join(json.dumps(event) for event in [assistant, assistant, result])
    parsed, calls = eval_output.parse_output(output)
    assert calls == {"nt4_watch": 1}
    record = eval_output.record_run(1, 1, 0.0, "question", "4", {**parsed, "mcp_tool_calls": calls})
    assert record["correct"] and record["usage"]["input_tokens"] == 15
    assert record["usage"]["output_tokens"] is None


def test_missing_metrics_are_not_reported_as_zero():
    parsed, calls = eval_output.parse_output('{"type":"result","result":"<response>4</response>"}')
    assert calls is None
    record = eval_output.record_run(1, 1, None, "question", "4", parsed)
    totals = eval_output.aggregate([record])
    assert totals["correct"] == 1
    assert totals["mcp_tool_calls"] is None and totals["total_cost_usd"] is None
    assert all(value is None for value in totals["usage"].values())
    assert "unavailable" in eval_output.render_report([record], totals)


def test_ambiguous_tool_event_and_invalid_output():
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__nt4__nt4_watch"},
        ]}},
        {"type": "result", "result": "ok"},
    ]
    assert eval_output.parse_output("\n".join(json.dumps(e) for e in events))[1] is None
    with pytest.raises(ValueError, match="no result"):
        eval_output.parse_output('{"type":"assistant"}')


def test_runner_collects_stream_metrics_without_calling_a_model(tmp_path, monkeypatch):
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__nt4__nt4_list_topics", "id": "1"},
        ]}},
        {"type": "result", "result": "<response>4</response>", "usage": {"output_tokens": 9}},
    ]

    def fake_run(cmd, **kwargs):
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in cmd and "question" in kwargs["input"]
        return SimpleNamespace(stdout="\n".join(json.dumps(e) for e in events), stderr="", returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.run_one("question", tmp_path / "cfg", None, tmp_path, 3)
    assert result["mcp_tool_calls"] == {"nt4_list_topics": 1}
    assert result["usage"]["output_tokens"] == 9 and result["wall_time_s"] >= 0


def test_runner_reports_timeout_without_inventing_metrics(tmp_path, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("claude", 3)

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    result = runner.run_one("question", tmp_path / "cfg", None, tmp_path, 3)
    record = eval_output.record_run(1, 1, None, "question", "4", result)
    assert record["error"] and not record["correct"]
    assert record["total_cost_usd"] is None and record["mcp_tool_calls"] is None


def test_repeated_phases_produce_machine_readable_per_run_metrics(tmp_path, monkeypatch):
    phases = []
    configs = []

    @contextlib.contextmanager
    def fake_robot(port, phase):
        phases.append((port, phase))
        yield

    def run_one(_question, cfg, _model, _cwd, _timeout):
        configs.append(json.loads(cfg.read_text()))
        return {
            "result": "<summary>steps</summary><feedback>feedback</feedback><response>4</response>",
            "mcp_tool_calls": {"mcp__nt4__nt4_watch": 2},
            "usage": {"input_tokens": 10, "output_tokens": 3},
            "total_cost_usd": 0.01, "num_turns": 2, "duration_ms": 1000, "wall_time_s": 1.1,
        }

    report, data = tmp_path / "report.md", tmp_path / "report.json"
    monkeypatch.setattr(runner, "fake_robot", fake_robot)
    monkeypatch.setattr(runner, "run_one", run_one)
    monkeypatch.setattr(sys, "argv", ["eval", "--fake-robot", "--eval-file",
                                     str(runner.ROOT / "evaluations" / "nt4_workflow_eval.xml"),
                                     "--only", "1", "--repeat", "2", "--phases", "0", "3.5",
                                     "--report", str(report), "--json-report", str(data)])
    assert runner.main() == 0
    result = json.loads(data.read_text())
    assert phases == [(5820, 0.0), (5820, 0.0), (5820, 3.5), (5820, 3.5)]
    assert result["totals"]["correct"] == 4 and result["totals"]["mcp_tool_calls"] == 8
    assert result["totals"]["usage"]["input_tokens"] == 40
    assert result["totals"]["usage"]["cache_read_input_tokens"] is None
    assert result["totals"]["total_cost_usd"] == pytest.approx(0.04)
    assert "Accuracy: 4/4" in report.read_text()
    assert all(c["mcpServers"][runner.SERVER]["env"]["NT4_WRITE_PREFIXES"] == FAKE_WRITE_PREFIXES for c in configs)


def test_student_evaluation_forces_read_only_fake_robot(tmp_path, monkeypatch):
    configs = []
    monkeypatch.setattr(runner, "fake_robot", lambda *_args: contextlib.nullcontext())

    def run_one(_question, cfg, _model, _cwd, _timeout):
        configs.append(json.loads(cfg.read_text()))
        return {"result": "<response>/Fake/Elevator/Current</response>"}

    monkeypatch.setattr(runner, "run_one", run_one)
    monkeypatch.setattr(sys, "argv", ["eval", "--fake-robot", "--read-only", "--eval-file",
                                     str(runner.ROOT / "evaluations" / "nt4_student_eval.xml"),
                                     "--only", "1", "--report", str(tmp_path / "report.md")])
    assert runner.main() == 0
    env = configs[0]["mcpServers"][runner.SERVER]["env"]
    assert {key: env[key] for key in safety_environment(None)} == safety_environment(None)


@pytest.mark.slow
@pytest.mark.parametrize("phase", [0.0, 2.5, 4.9])
async def test_student_scenarios_match_a_real_fake_robot(monkeypatch, phase):
    from fastmcp import Client

    from frc_mcp_nt4.spec import SPEC

    mcp, _ = SPEC.load()

    questions = ET.parse(runner.ROOT / "evaluations" / "nt4_student_eval.xml").getroot().findall("qa_pair")
    assert [q.findtext("answer") for q in questions] == [
        "/Fake/Elevator/Current", "Center", "Limit", "YES", "NO", "NO",
    ]
    monkeypatch.setenv("NT4_READ_ONLY", "1")
    monkeypatch.delenv("NT4_ALLOW_WRITES", raising=False)
    monkeypatch.delenv("NT4_WRITE_PREFIXES", raising=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with support.fake_robot(port, phase):
        async with Client(mcp) as client:
            def value(result):
                return json.loads(result.content[0].text)

            status = value(await client.call_tool("nt4_connection", {"host": "127.0.0.1", "port": port}))
            assert status["writes_allowed"] is False
            topics = value(await client.call_tool("nt4_list_topics", {"name_contains": "elevator/current"}))
            assert topics["topics"] == {"/Fake/Elevator/Current": "double"}
            chooser = value(await client.call_tool("nt4_get_widgets", {"bases": ["/Fake/Auto Chooser"]}))
            assert chooser["/Fake/Auto Chooser"]["current"] == "Center"
            for setting, expected in (("Setpoint", True), ("Gain", True), ("Limit", False)):
                reply = value(await client.call_tool("nt4_inspect", {
                    "what": "subscribers", "target": f"/Fake/Tunable/{setting}",
                }))
                rows = reply["targets"][f"/Fake/Tunable/{setting}"]["rows"]
                assert any(row.get("client") == "(server)" for row in rows) is expected
            capture = value(await client.call_tool("nt4_capture_start", {
                "topics": ["/Fake/Elevator/Current"], "all_changes": True, "duration_s": 9,
                "event_rules": [{"topic": "/Fake/Elevator/Current", "op": ">", "value": 40}],
            }))
            query = {"capture_id": capture["id"], "events_after": 0}
            await wait_for_tool(client, "nt4_capture_query", query, lambda r: r["events"]["observed"] >= 1)
            await client.call_tool("nt4_capture_stop", {"capture_id": capture["id"]})
            events = value(await client.call_tool("nt4_capture_query", query))["events"]
            await client.call_tool("nt4_capture_stop", {"capture_id": capture["id"], "discard": True})
            assert events["observed"] >= 1 and events["missed"] == 0
            angle = value(await client.call_tool("nt4_get_values", {
                "topics": ["/Fake/Arm/Angle"], "include_age": True,
            }))
            assert "last_update" in angle["/Fake/Arm/Angle"]


@pytest.mark.parametrize("args", [
    ["--fake-robot", "--host", "192.0.2.1"],
    ["--phases", "1"], ["--fake-robot", "--phases", "nan"],
    ["--repeat", "0"], ["--port", "0"], ["--only", "0"], ["--only", "999"],
    ["--read-only", "--write-prefixes", "/Fake/Test"],
    ["--adapter", "example"], ["--adapter-arg", "example"],
    ["--fake-robot", "--phases", "0", "0"], ["--only", "1", "1"],
    ["--server-label", " "],
])
def test_invalid_runner_options_fail_before_starting_any_agent(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["eval", *args])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("contents", ["<evaluation>", "<evaluation><qa_pair><question>x</question></qa_pair></evaluation>"])
def test_invalid_evaluation_file_fails_before_agent_start(tmp_path, monkeypatch, capsys, contents):
    path = tmp_path / "invalid.xml"
    path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["eval", "--eval-file", str(path)])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2 and "error:" in capsys.readouterr().err


def test_fake_robot_readiness_marker_uses_the_started_process():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with support.fake_robot(port, 3.5):
        client = ntcore.NetworkTableInstance.create()
        try:
            marker = client.getStringTopic("/Fake/Evaluation/RunId").subscribe("")
            client.startClient4("evaluation-test")
            client.setServer("127.0.0.1", port)
            deadline = time.monotonic() + 3
            while not marker.get() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert len(marker.get()) == 32
        finally:
            ntcore.NetworkTableInstance.destroy(client)


def test_fake_robot_does_not_accept_an_existing_server_as_its_own():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with support.fake_robot(port, 0), pytest.raises(RuntimeError, match="fake robot"), support.fake_robot(port, 0, readiness_timeout=1):
        pytest.fail("accepted the wrong robot")

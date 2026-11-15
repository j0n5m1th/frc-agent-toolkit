import copy
import json
import socket
import subprocess
import sys
from types import SimpleNamespace

import compare_reports
import eval_output
import pytest
import run_with_claude_code as runner


def report(client="client", phase=0.0):
    result = {
        "result": "<response>answer</response>", "mcp_tool_calls": {"nt4_watch": 2},
        "usage": {"input_tokens": 10, "output_tokens": 2}, "total_cost_usd": 0.1, "wall_time_s": 1.0,
    }
    return {
        "schema_version": 2, "client": client, "model": "model",
        "eval_sha256": "a" * 64, "prompt_sha256": "b" * 64,
        "conditions": {"target": "fake-robot", "write_prefixes": None,
                       "timeout_seconds": 30, "server_label": "rev1"},
        "runs": [eval_output.record_run(1, 1, phase, "question", "answer", result)],
        "totals": {"correct": 999},
    }


def test_comparison_recomputes_totals_and_keeps_unknown_metrics():
    first, second = report("first"), report("second")
    second["runs"][0]["usage"]["input_tokens"] = None
    second["runs"][0]["mcp_tool_calls"] = None
    text = compare_reports.compare([first, second])
    assert "| first | model | 1/1 | 2 | 10 | 2 | unavailable | 0.1 | 1.0 |" in text
    assert "| second | model | 1/1 | unavailable | unavailable | 2 |" in text
    assert "999" not in text and "client/user-reported" in text
    first["conditions"]["server_label"] = second["conditions"]["server_label"] = None
    assert "equivalence is unverified" in compare_reports.compare([first, second])


@pytest.mark.parametrize("change", ["eval", "prompt", "target", "policy", "timeout", "server", "phase", "question", "duplicate"])
def test_comparison_rejects_mismatched_runs(change):
    first, second = report(), report("second")
    if change == "eval":
        second["eval_sha256"] = "c" * 64
    elif change == "prompt":
        second["prompt_sha256"] = "c" * 64
    elif change == "target":
        second["conditions"]["target"] = "other"
    elif change == "policy":
        second["conditions"]["write_prefixes"] = "/Fake"
    elif change == "timeout":
        second["conditions"]["timeout_seconds"] = 20
    elif change == "server":
        second["conditions"]["server_label"] = "rev2"
    elif change == "phase":
        second["runs"][0]["phase_seconds"] = 2
    elif change == "question":
        second["runs"][0]["question"] = "different question"
    else:
        second["runs"].append(copy.deepcopy(second["runs"][0]))
    with pytest.raises(ValueError):
        compare_reports.compare([first, second])


@pytest.mark.parametrize("field,value", [
    ("mcp_tool_calls", {"nt4_watch": -1}),
    ("mcp_tool_calls", {"nt4_watch": True}),
    ("total_cost_usd", -1),
    ("correct", False),
])
def test_comparison_rejects_malformed_metrics(field, value):
    first, second = report(), report("second")
    second["runs"][0][field] = value
    with pytest.raises(ValueError):
        compare_reports.compare([first, second])


@pytest.mark.parametrize("data", [
    "not-json", "[]", "{}", '{"result":"ok","mcp_tool_calls":{"nt4_watch":-1}}',
    '{"result":"ok","usage":{"input_tokens":NaN}}', '{"result":"ok","total_cost_usd":true}',
])
def test_adapter_reports_invalid_output_instead_of_inventing_metrics(tmp_path, monkeypatch, data):
    monkeypatch.setattr(runner.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        stdout=data, stderr="", returncode=0,
    ))
    result = runner.run_adapter(["adapter"], "q", tmp_path / "cfg", None, tmp_path, 3)
    assert "Invalid adapter response" in result["error"]
    assert eval_output.record_run(1, 1, 0.0, "q", "yes", result)["mcp_tool_calls"] is None


def test_adapter_reports_launch_failure(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise PermissionError("cannot launch adapter")

    monkeypatch.setattr(runner.subprocess, "run", fail)
    result = runner.run_adapter(["adapter"], "q", tmp_path / "cfg", None, tmp_path, 3)
    assert "cannot launch adapter" in result["error"]


def test_fake_adapter_roundtrip_with_fresh_robot_and_comparison(tmp_path, monkeypatch, capsys):
    # This stub exercises the adapter protocol, not an actual model or MCP client.
    code = (
        "import json,sys; req=json.load(sys.stdin); cfg=json.load(open(req['mcp_config'],encoding='utf-8')); "
        "env=cfg['mcpServers']['nt4']['env']; "
        "assert req['protocol_version']==1 and env['NT4_READ_ONLY']=='1' and env['NT4_ALLOW_WRITES']=='0'; "
        "print(json.dumps({'result':'<response>/Fake/Elevator/Current</response>',"
        "'mcp_tool_calls':{},'usage':{'input_tokens':7},'total_cost_usd':0}))"
    )
    file = tmp_path / "adapter-report.json"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(sys, "argv", [
        "eval", "--fake-robot", "--read-only", "--eval-file", str(runner.ROOT / "evaluations" / "nt4_student_eval.xml"),
        "--port", str(port), "--only", "1", "--phases", "0", "2.5", "--adapter", sys.executable,
        "--adapter-arg=-c", f"--adapter-arg={code}", "--client-label", "stub", "--server-label", "test",
        "--json-report", str(file), "--report", str(tmp_path / "report.md"),
    ])
    assert runner.main() == 0
    result = json.loads(file.read_text())
    assert result["schema_version"] == 2 and result["conditions"]["write_prefixes"] is None
    assert result["client"] == "stub" and result["totals"]["correct"] == 2
    assert result["totals"]["usage"]["input_tokens"] == 14
    other = copy.deepcopy(result)
    other["client"] = "other-stub"
    assert "| stub |" in compare_reports.compare([result, other])
    assert "stub" in (tmp_path / "report.md").read_text()


def test_comparison_command_rejects_mismatched_files(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(report()), encoding="utf-8")
    b.write_text(json.dumps(report("other")), encoding="utf-8")
    command = [sys.executable, str(runner.ROOT / "evaluations" / "compare_reports.py"), str(a), str(b)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "| other |" in result.stdout
    b.write_text(json.dumps(report("other", phase=1)), encoding="utf-8")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and "questions, phases or repetitions differ" in result.stderr

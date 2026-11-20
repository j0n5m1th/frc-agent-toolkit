"""The log server's evaluation: every expected answer is recomputed from the practice log, and the runner can target it."""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import fake_log
import pytest
import run_with_claude_code as runner
import runner_support

from frc_mcp_wpilog import queries, timeline
from frc_mcp_wpilog.logfile import LogFile
from frc_mcp_wpilog.queries import QuerySpec, Range
from frc_mcp_wpilog.scan_queries import scan

EVAL = Path(__file__).resolve().parents[2] / "evaluations" / "wpilog_eval.xml"


@pytest.fixture
def practice(tmp_path):
    return LogFile(fake_log.build().save(tmp_path / "practice.wpilog"))


def expected_answers(log):
    """What each question's answer is, worked out directly from the log (not copied from the XML)."""
    auto = next(p for p in timeline.phases(log) if p.mode == "auto")
    teleop = next(p for p in timeline.phases(log) if p.mode == "teleop")
    auto_ms = (auto.end_us - auto.start_us) / 1000
    spikes = queries.when(log, "/Fake/Elevator/Current", ">", 40, None, Range("auto", 0, auto_ms), 20)
    voltage = queries.query(log, ["/Fake/Battery/Voltage"], QuerySpec(reduce="summary"), Range())["data"]["/Fake/Battery/Voltage"]
    speed = queries.query(log, ["/Fake/Drive/Speeds"], QuerySpec(reduce="summary", fields=["vx"]), Range("auto", 0, auto_ms))
    pose = queries.values_at(log, ["/Fake/Drive/Pose"], 10000, ["translation.x"], "auto")
    error = queries.search_text(log, None, "error", Range(), 5)["items"][0]
    sampled = queries.when(log, "/Fake/Elevator/Current", ">", 40, None, Range("auto", 0, auto_ms), 20, (), ["/Fake/Arm/Angle"])
    by_mode = queries.by_mode(log, ["/Fake/Drive/Speeds"], QuerySpec(reduce="summary", fields=["vx"]))["modes"]
    peak = {m: by_mode[m]["data"]["/Fake/Drive/Speeds"]["fields"]["vx"]["max"] for m in ("auto", "teleop")}
    found = scan(log)["messages"]
    return [
        str(round(log.duration_s)),
        str(spikes["n"]),
        str(voltage["min"]),
        str(round(spikes["intervals_ms"][0][0] / 1000)),
        str(round(error["t_ms"] / 1000)),
        str(round(speed["data"]["/Fake/Drive/Speeds"]["fields"]["vx"]["max"])),
        str(round((teleop.end_us - teleop.start_us) / 1e6)),
        str(round(pose["/Fake/Drive/Pose"]["v"]["translation.x"])),
        str(round(sampled["sampled"]["/Fake/Arm/Angle"][0])),
        str(round(peak["auto"] / peak["teleop"])),
        str(found["errors"] + found["warnings"]),
    ]


def test_every_expected_answer_in_the_xml_matches_the_practice_log(practice):
    pairs = [(q.findtext("question"), q.findtext("answer")) for q in ET.parse(EVAL).getroot().iter("qa_pair")]
    assert [answer for _, answer in pairs] == expected_answers(practice)
    assert all("practice log" in question for question, _ in pairs)


def test_the_runner_parses_the_evaluation_file():
    pairs = runner.parse_eval_pairs(EVAL.read_bytes())
    assert len(pairs) == 11 and all(question and answer for question, answer in pairs)


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["run_with_claude_code.py", *argv])
    ap = runner.build_parser()
    args = ap.parse_args()
    return ap, args


def test_practice_log_mode_defaults_to_the_log_evaluation_and_the_log_server(monkeypatch):
    ap, args = run(monkeypatch, "--practice-log")
    runner.validate_args(ap, args)
    assert args.eval_file.name == "wpilog_eval.xml" and runner.write_prefixes(args) is None
    ap, args = run(monkeypatch)
    runner.validate_args(ap, args)
    assert args.eval_file.name == "last_sim_eval.xml"


@pytest.mark.parametrize("extra", [["--fake-robot"], ["--port", "5811"], ["--host", "10.0.0.2"], ["--write-prefixes", "/x"]])
def test_practice_log_mode_refuses_options_that_belong_to_the_live_server(monkeypatch, extra, capsys):
    ap, args = run(monkeypatch, "--practice-log", *extra)
    with pytest.raises(SystemExit):
        runner.validate_args(ap, args)
    assert "--practice-log runs the read-only log server" in capsys.readouterr().err


def test_the_log_server_config_launches_frc_mcp_wpilog_with_the_practice_folder(tmp_path):
    with runner_support.practice_log() as env:
        folder = Path(env["WPILOG_DIR"])
        assert (folder / "practice.wpilog").is_file()
        cfg = tmp_path / "mcp.json"
        runner_support.log_server_config(cfg, env)
    server = json.loads(cfg.read_text())["mcpServers"]
    assert list(server) == ["wpilog"] and server["wpilog"]["args"][-2:] == ["frc-mcp", "wpilog"]
    assert server["wpilog"]["env"] == env and not folder.exists()  # the folder is removed afterwards


def test_run_one_allows_only_the_chosen_servers_tools(monkeypatch, tmp_path):
    seen = {}

    def fake_run(command, stdin_text, cwd, timeout, launch_errors):
        seen["command"] = command
        return None, 0.0, "stop here"

    monkeypatch.setattr(runner, "_run_command", fake_run)
    runner.run_one("q", tmp_path / "c.json", None, tmp_path, 5, "wpilog")
    assert "mcp__wpilog__*" in seen["command"]
    runner.run_one("q", tmp_path / "c.json", None, tmp_path, 5)
    assert "mcp__nt4__*" in seen["command"]


def test_tool_calls_are_counted_under_the_log_servers_prefix():
    import eval_output

    events = [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "a", "name": "mcp__wpilog__wpilog_open"},
        {"type": "tool_use", "id": "b", "name": "mcp__nt4__nt4_status"}]}},
        {"type": "result", "result": "ok"}]
    output = "\n".join(json.dumps(e) for e in events)
    assert eval_output.parse_output(output, "wpilog")[1] == {"wpilog_open": 1}
    assert eval_output.parse_output(output)[1] == {"nt4_status": 1}  # the default is still the NT4 server

"""Run an evaluation XML through headless Claude Code (`claude -p`) instead of the Anthropic API.

Uses your existing Claude Code login (no ANTHROPIC_API_KEY). Each question runs in a fresh
session that can only see this MCP server's tools (built-in tools disabled, no other MCP
servers, no project settings: it runs from an empty temp directory). Grading is exact string match.

    # read-only evaluation of YOUR robot's simulation (listening on 127.0.0.1:5810). First write the questions from it:
    #   uv run --project plugins/frc python evaluations/make_sim_eval.py
    uv run --project plugins/frc python evaluations/run_with_claude_code.py [--only N ...] [--model M] [--report FILE]

    # workflow evaluation: starts a fresh evaluations/fake_robot.py for every question and allows
    # writes only under /Fake/Tunable/, /Fake/Notes/ and /Fake/Auto Chooser/
    uv run --project plugins/frc python evaluations/run_with_claude_code.py --fake-robot --eval-file evaluations/nt4_workflow_eval.xml

    # the log server (frc-mcp wpilog) against the deterministic practice log from evaluations/fake_log.py
    uv run --project plugins/frc python evaluations/run_with_claude_code.py --practice-log
"""

import argparse
import contextlib
import hashlib
import json
import math
import subprocess  # nosec B404 - runs the claude CLI / user adapter with fixed argv lists, never a shell
import sys
import tempfile
import time
import xml.etree.ElementTree as ET  # nosec B405 - only parses repo-local evaluation files
from pathlib import Path

from eval_common import (
    LOG_SERVER,
    MAX_PORT,
    MIN_PORT,
    ROOT,
    SCHEMA_VERSION,
    SERVER,
    atomic_write_text,
)
from eval_output import (
    aggregate,
    parse_adapter_response,
    parse_output,
    record_run,
    render_report,
)
from runner_support import (
    FAKE_WRITE_PREFIXES,
    fake_robot,
    log_server_config,
    practice_log,
    server_environment,
    write_mcp_config,
)

DEFAULT_SIM_PORT, DEFAULT_FAKE_PORT = 5810, 5820
DEFAULT_TIMEOUT_S = 420
ERROR_TEXT_LIMIT = 500  # characters of stderr/exception text kept in a run's error field
MAX_BUDGET_USD = "2"

PROMPT = """You are an AI assistant with access to tools (only the tools provided).

Complete the task using the tools. Then reply in exactly this format:
<summary>Steps taken: which tools, in what order, with what inputs, and what they returned. Be brief.</summary>
<feedback>Constructive feedback on the tools: names, parameter docs, required vs optional, descriptions, return
formats, errors, anything confusing, anything that wasted effort. Be specific.</feedback>
<response>ONLY the final answer, in the format the question asks for, nothing else.</response>

Task: {question}
"""


def _run_command(command: list[str], stdin_text: str, cwd: Path, timeout: int,
                 launch_errors: tuple[type[BaseException], ...]) -> tuple[subprocess.CompletedProcess[str] | None, float, str | None]:
    """Shared subprocess scaffolding: the process (None when it did not start), elapsed seconds, error text."""
    start = time.monotonic()
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv lists (claude CLI / user-chosen adapter), no shell
            command, input=stdin_text, capture_output=True, text=True,
            cwd=cwd, timeout=timeout, encoding="utf-8")
    except launch_errors as e:
        return None, time.monotonic() - start, str(e)[:ERROR_TEXT_LIMIT]
    return proc, time.monotonic() - start, None


def run_adapter(command: list[str], question: str, cfg: Path, model: str | None, cwd: Path,
                timeout: int) -> dict[str, object]:
    """Run a user-supplied client adapter without a shell; metrics remain client-reported."""
    request = {
        "protocol_version": 1, "question": question, "prompt": PROMPT.format(question=question),
        "mcp_config": str(cfg), "model": model, "timeout_seconds": timeout,
    }
    proc, elapsed, error = _run_command(
        command, json.dumps(request), cwd, timeout,
        (subprocess.TimeoutExpired, OSError, UnicodeError))
    if proc is None:
        return {"error": error, "wall_time_s": elapsed}
    if proc.returncode:
        return {"error": (proc.stderr or f"Adapter exited with code {proc.returncode}")[:ERROR_TEXT_LIMIT],
                "wall_time_s": elapsed}
    try:
        data = parse_adapter_response(proc.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        return {"error": f"Invalid adapter response: {e}", "wall_time_s": elapsed}
    data["wall_time_s"] = elapsed
    return data


def run_one(question: str, cfg: Path, model: str | None, cwd: Path, timeout: int, server: str = SERVER) -> dict[str, object]:
    cmd = [
        "claude", "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence", "--disable-slash-commands",
        "--mcp-config", str(cfg), "--strict-mcp-config",
        "--tools", "",  # no built-in tools: only the MCP server
        "--allowedTools", f"mcp__{server}__*",
        "--max-budget-usd", MAX_BUDGET_USD,
    ]
    if model:
        cmd += ["--model", model]
    # prompt on stdin: the variadic flags above would otherwise swallow a positional prompt
    proc, elapsed, error = _run_command(
        cmd, PROMPT.format(question=question), cwd, timeout,
        (subprocess.TimeoutExpired, FileNotFoundError))
    if proc is None:
        return {"error": error, "wall_time_s": elapsed}
    try:
        res, calls = parse_output(proc.stdout, server)
    except (json.JSONDecodeError, ValueError):
        return {"error": (proc.stderr or proc.stdout or "Claude returned no output")[:ERROR_TEXT_LIMIT],
                "wall_time_s": elapsed}
    res["mcp_tool_calls"] = calls
    res["wall_time_s"] = elapsed
    if res.get("is_error") or proc.returncode != 0:
        # e.g. "Not logged in · Please run /login": the CLI's login is separate from the desktop app's
        res["error"] = str(res.get("result") or proc.stderr or "Claude failed")[:ERROR_TEXT_LIMIT]
    return res


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-file", type=Path, default=None,
                    help="default: evaluations/last_sim_eval.xml, which make_sim_eval.py writes from your own simulation "
                         "(evaluations/wpilog_eval.xml with --practice-log)")
    ap.add_argument("--only", type=int, nargs="+", help="1-based question numbers")
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--json-report", type=Path, default=None, help="write machine-readable per-run metrics")
    ap.add_argument("--adapter", help="user-provided client adapter executable (instead of Claude Code)")
    ap.add_argument("--adapter-arg", action="append", default=[], help="argument passed to adapter; repeat as needed")
    ap.add_argument("--client-label", help="required when using --adapter")
    ap.add_argument("--server-label", help="identify the server revision for comparison (user-supplied)")
    ap.add_argument("--repeat", type=int, default=1, help="repeat each question at each phase")
    ap.add_argument("--phases", type=float, nargs="+", default=None,
                    help="fake-robot starting signal offsets in seconds (default: 0)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None,
                    help=f"NT4 port (default {DEFAULT_SIM_PORT}, or {DEFAULT_FAKE_PORT} with --fake-robot)")
    ap.add_argument("--fake-robot", action="store_true", help="start a fresh fake robot per question")
    ap.add_argument("--practice-log", action="store_true",
                    help="evaluate the log server (frc-mcp wpilog) on the deterministic practice log instead of the NT4 server")
    safety = ap.add_mutually_exclusive_group()
    safety.add_argument("--write-prefixes", default=None, help="allow writes under these comma-separated prefixes")
    safety.add_argument("--read-only", action="store_true", help="disable writes even in fake-robot mode")
    return ap


def _check_label_options(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.adapter and not (args.client_label and args.client_label.strip()):
        ap.error("--adapter requires --client-label")
    if not args.adapter and (args.adapter_arg or args.client_label):
        ap.error("--adapter-arg and --client-label require --adapter")
    if args.server_label is not None and not args.server_label.strip():
        ap.error("--server-label cannot be empty")


def _check_phase_options(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.phases is None:
        return
    if not args.fake_robot:
        ap.error("--phases requires --fake-robot")
    if not all(math.isfinite(phase) for phase in args.phases):
        ap.error("--phases must be finite")
    if len(set(args.phases)) != len(args.phases):
        ap.error("--phases must not contain duplicates")


def _check_practice_log(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """The log server is read-only, so the nt4-only options cannot accompany it."""
    if args.practice_log and (args.fake_robot or args.port is not None or args.host != "127.0.0.1" or args.write_prefixes):
        ap.error("--practice-log runs the read-only log server: it cannot be combined with --fake-robot, --host, --port or --write-prefixes")


def _chosen_port(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """The port to use: the one given, or the default of whichever server runs."""
    port = args.port if args.port is not None else (DEFAULT_FAKE_PORT if args.fake_robot else DEFAULT_SIM_PORT)
    if not MIN_PORT <= port <= MAX_PORT:
        ap.error(f"--port must be between {MIN_PORT} and {MAX_PORT}")
    return port


def validate_args(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Reject inconsistent options via ap.error (exit status 2) and return the NT4 port to use."""
    _check_label_options(ap, args)
    if args.repeat < 1 or args.timeout < 1:
        ap.error("--repeat and --timeout must be positive")
    _check_phase_options(ap, args)
    _check_practice_log(ap, args)
    if args.eval_file is None:
        args.eval_file = ROOT / "evaluations" / ("wpilog_eval.xml" if args.practice_log else "last_sim_eval.xml")
    if args.fake_robot and args.host != "127.0.0.1":
        ap.error("--fake-robot only supports --host 127.0.0.1")
    return _chosen_port(ap, args)


def parse_eval_pairs(eval_bytes: bytes) -> list[tuple[str, str]]:
    """The (question, answer) pairs of an evaluation XML; raises ET.ParseError or ValueError when invalid."""
    root = ET.fromstring(eval_bytes)  # nosec B314 - parses repo-local eval files
    pairs = []
    for pair in root.iter("qa_pair"):
        question, answer = pair.findtext("question"), pair.findtext("answer")
        if not question or not answer or not question.strip() or not answer.strip():
            raise ValueError("every evaluation pair must have a nonempty question and answer")
        pairs.append((question.strip(), answer.strip()))
    return pairs


def load_evaluation(ap: argparse.ArgumentParser, eval_file: Path) -> tuple[bytes, list[tuple[str, str]]]:
    try:
        eval_bytes = eval_file.read_bytes()
        return eval_bytes, parse_eval_pairs(eval_bytes)
    except (OSError, ET.ParseError) as e:
        hint = " (write it from your own simulation first: evaluations/make_sim_eval.py)" if eval_file.name == "last_sim_eval.xml" else ""
        ap.error(f"cannot read evaluation: {e}{hint}")
    except ValueError as e:
        ap.error(str(e))


def select_questions(ap: argparse.ArgumentParser, only: list[int] | None, count: int) -> list[int]:
    sel = only or list(range(1, count + 1))
    if not count or any(n < 1 or n > count for n in sel) or len(sel) != len(set(sel)):
        ap.error("evaluation must contain questions and --only numbers must be unique and in range")
    return sel


def write_prefixes(args: argparse.Namespace) -> str | None:
    """Comma-separated NT4 prefixes the server may write under: none for read-only runs."""
    if args.read_only or args.practice_log:
        return None
    return args.write_prefixes or (FAKE_WRITE_PREFIXES if args.fake_robot else None)


def run_questions(args: argparse.Namespace, pairs: list[tuple[str, str]], sel: list[int],
                  phases: list[float | None], port: int, cfg: Path, workdir: Path) -> list[dict[str, object]]:
    records = []
    for n in sel:
        question, expected = pairs[n - 1]
        for phase in phases:
            for iteration in range(1, args.repeat + 1):
                print(f"[{n}/{len(pairs)} phase={phase} repeat={iteration}] running...", file=sys.stderr, flush=True)
                with fake_robot(port, phase) if args.fake_robot else contextlib.nullcontext():
                    if args.adapter:
                        res = run_adapter([args.adapter, *args.adapter_arg], question, cfg, args.model, workdir, args.timeout)
                    else:
                        server = (LOG_SERVER,) if args.practice_log else ()  # the NT4 server is run_one's default
                        res = run_one(question, cfg, args.model, workdir, args.timeout, *server)
                record = record_run(n, iteration, phase, question, expected, res)
                records.append(record)
                print(f"    {'PASS' if record['correct'] else 'FAIL'} expected={expected!r} got={record['answer']!r}",
                      file=sys.stderr)
    return records


def build_json_report(args: argparse.Namespace, eval_bytes: bytes, port: int, prefixes: str | None,
                      totals: dict[str, object], records: list[dict[str, object]]) -> str:
    return json.dumps({
        "schema_version": SCHEMA_VERSION, "client": args.client_label or "claude-code", "model": args.model,
        "eval_file": str(args.eval_file), "eval_sha256": hashlib.sha256(eval_bytes).hexdigest(),
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "conditions": {"target": "practice-log" if args.practice_log else "fake-robot" if args.fake_robot else f"{args.host}:{port}",
                       "write_prefixes": prefixes, "timeout_seconds": args.timeout,
                       "server_label": args.server_label},
        "fake_robot": args.fake_robot, "totals": totals, "runs": records,
    }, indent=2, allow_nan=False) + "\n"


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    port = validate_args(ap, args)
    prefixes = write_prefixes(args)
    env = server_environment(args.host, port, prefixes)

    eval_bytes, pairs = load_evaluation(ap, args.eval_file)
    sel = select_questions(ap, args.only, len(pairs))
    phases = (args.phases or [0.0]) if args.fake_robot else [None]

    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
        cfg = Path(tmp) / "mcp.json"
        if args.practice_log:
            log_server_config(cfg, stack.enter_context(practice_log()))
        else:
            write_mcp_config(cfg, env)
        workdir = Path(tmp) / "work"
        workdir.mkdir()
        records = run_questions(args, pairs, sel, phases, port, cfg, workdir)
        totals = aggregate(records)
        report = render_report(records, totals, args.client_label or "Claude Code headless")
    if args.report:
        atomic_write_text(args.report, report)
    else:
        print(report)
    if args.json_report:
        atomic_write_text(args.json_report, build_json_report(args, eval_bytes, port, prefixes, totals, records))
    return 0 if totals["correct"] == totals["runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

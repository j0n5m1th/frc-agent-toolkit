"""Compare evaluation reports only when their scenarios and run conditions match."""

import argparse
import json
import sys
from pathlib import Path

from eval_common import (
    SCHEMA_VERSION,
    USAGE_KEYS,
    number,
    reject_constant,
    valid_tool_call_counts,
)
from eval_output import aggregate

Case = tuple[str, str]
CaseKey = tuple[int, int, float | None]
SHA256_HEX_LENGTH = 64
CONDITION_KEYS = {"target", "write_prefixes", "timeout_seconds", "server_label"}
MEASUREMENT_FIELDS = {"error", "mcp_tool_calls", "wall_time_s", "total_cost_usd"}


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _validate_header(report: dict[str, object]) -> None:
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"expected an evaluation report with schema_version {SCHEMA_VERSION}")
    if not isinstance(report.get("client"), str) or not report["client"].strip():
        raise ValueError("report client must be a nonempty string")
    if report.get("model") is not None and not isinstance(report["model"], str):
        raise ValueError("invalid model")
    for key in ("eval_sha256", "prompt_sha256"):
        value = report.get(key)
        if not isinstance(value, str) or len(value) != SHA256_HEX_LENGTH or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"invalid {key}")


def _validate_conditions(conditions: object) -> None:
    if not isinstance(conditions, dict) or set(conditions) != CONDITION_KEYS:
        raise ValueError("invalid run conditions")
    if not isinstance(conditions["target"], str) or not conditions["target"]:
        raise ValueError("invalid target")
    if conditions["write_prefixes"] is not None and not isinstance(conditions["write_prefixes"], str):
        raise ValueError("invalid write prefixes")
    if number(conditions["timeout_seconds"]) is None or conditions["timeout_seconds"] <= 0:
        raise ValueError("invalid timeout")
    if conditions["server_label"] is not None and not isinstance(conditions["server_label"], str):
        raise ValueError("invalid server label")


def _validate_case_key(record: dict[str, object]) -> CaseKey:
    n, repeat, phase = record.get("question_number"), record.get("repeat"), record.get("phase_seconds")
    if not (_is_positive_int(n) and _is_positive_int(repeat)):
        raise ValueError("invalid question number or repetition")
    if phase is not None and (not isinstance(phase, (int, float)) or isinstance(phase, bool) or number(abs(phase)) is None):
        raise ValueError("invalid phase")
    return n, repeat, phase


def _validate_outcome(record: dict[str, object]) -> None:
    for field in ("question", "expected", "answer"):
        if not isinstance(record.get(field), str):
            raise ValueError(f"invalid {field}")
    if not record.keys() >= MEASUREMENT_FIELDS:
        raise ValueError("run is missing measurement fields")
    error = record.get("error")
    if error is not None and not isinstance(error, str):
        raise ValueError("invalid run error")
    if not isinstance(record.get("correct"), bool) or record["correct"] != (not error and record["answer"] == record["expected"]):
        raise ValueError("invalid correctness result")


def _validate_measurements(record: dict[str, object]) -> None:
    if not valid_tool_call_counts(record.get("mcp_tool_calls")):
        raise ValueError("invalid tool-call counts")
    for field in ("wall_time_s", "total_cost_usd"):
        if record.get(field) is not None and number(record[field]) is None:
            raise ValueError(f"invalid {field}")
    usage = record.get("usage")
    if not isinstance(usage, dict) or any(key not in usage for key in USAGE_KEYS) or any(
        usage[key] is not None and number(usage[key]) is None for key in USAGE_KEYS
    ):
        raise ValueError("invalid usage")


def _validate_report(report: dict[str, object]) -> dict[CaseKey, Case]:
    """Check a report's structure; returns its (question, expected) by (number, repeat, phase)."""
    _validate_header(report)
    _validate_conditions(report.get("conditions"))
    records = report.get("runs")
    if not isinstance(records, list) or not records:
        raise ValueError("report must contain runs")
    cases: dict[CaseKey, Case] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("invalid run")
        key = _validate_case_key(record)
        if key in cases:
            raise ValueError("duplicate question, phase and repetition")
        _validate_outcome(record)
        _validate_measurements(record)
        cases[key] = (record["question"], record["expected"])
    return cases


def display(value: float | int | None) -> str:
    return "unavailable" if value is None else str(round(value, 3) if isinstance(value, float) else value)


def compare(reports: list[dict[str, object]]) -> str:
    if len(reports) < 2:
        raise ValueError("provide at least two reports")
    cases = [_validate_report(report) for report in reports]
    reference = reports[0]
    for report, other_cases in zip(reports[1:], cases[1:], strict=True):
        if any(report[key] != reference[key] for key in ("eval_sha256", "prompt_sha256", "conditions")):
            raise ValueError("scenario, prompt or run conditions differ")
        if other_cases != cases[0]:
            raise ValueError("questions, phases or repetitions differ")
    lines = [
        "| Client | Model | Accuracy | MCP calls | Input tokens | Output tokens | Cache-read tokens | Cost USD | Wall seconds |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for report in reports:
        total = aggregate(report["runs"])
        client = report["client"].replace("|", "\\|").replace("\n", " ")
        model = str(report.get("model") or "unavailable").replace("|", "\\|").replace("\n", " ")
        usage = total["usage"]
        lines.append(
            f"| {client} | {model} | {total['correct']}/{total['runs']} | {display(total['mcp_tool_calls'])} | "
            f"{display(usage['input_tokens'])} | {display(usage['output_tokens'])} | "
            f"{display(usage['cache_read_input_tokens'])} | {display(total['total_cost_usd'])} | {display(total['wall_time_s'])} |"
        )
    lines.append("\nMetrics and server labels are client/user-reported; matching settings do not prove identical execution.")
    if not reference["conditions"]["server_label"]:
        lines.append("Server revision was not specified; cross-run server equivalence is unverified.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        reports = [json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant) for path in args.reports]
        print(compare(reports))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Cannot compare reports: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

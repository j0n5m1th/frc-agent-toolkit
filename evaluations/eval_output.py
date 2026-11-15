"""Parsing and validation of what Claude Code / an adapter prints (JSON, stream-json, adapter replies)."""

import json
import re
from collections.abc import Iterator

from eval_common import (
    SERVER,
    USAGE_KEYS,
    number,
    reject_constant,
    valid_tool_call_counts,
)


def _load_events(output: str) -> list[dict[str, object]]:
    """Events from a single JSON object or from stream-json (one object per line)."""
    try:
        events = [json.loads(output)]
    except json.JSONDecodeError:
        events = [json.loads(line) for line in output.splitlines() if line.strip()]
    if not all(isinstance(event, dict) for event in events):
        raise ValueError("Claude returned a non-object event")
    return events


def _result_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    results = [event for event in events if event.get("type") == "result"]
    if not results and len(events) == 1 and "result" in events[0]:
        results = events
    if not results:
        raise ValueError("Claude returned no result event")
    return results


def _assistant_blocks(events: list[dict[str, object]]) -> Iterator[list[object]]:
    """The content lists of assistant messages that carry one."""
    for event in events:
        if event.get("type") != "assistant" or not isinstance(event.get("message"), dict):
            continue
        content = event["message"].get("content")
        if isinstance(content, list):
            yield content


def _count_tool_uses(events: list[dict[str, object]], server: str = SERVER) -> dict[str, int] | None:
    """Distinct MCP tool uses by tool name; None when nothing was observed or an id is missing."""
    prefix = f"mcp__{server}__"
    counts: dict[str, int] = {}
    seen: set[str] = set()
    observed = False
    for content in _assistant_blocks(events):
        observed = True
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name, tool_id = block.get("name"), block.get("id")
            if not (isinstance(name, str) and name.startswith(prefix)):
                continue
            if not isinstance(tool_id, str) or not tool_id:
                return None
            if tool_id not in seen:
                seen.add(tool_id)
                tool_name = name.removeprefix(prefix)
                counts[tool_name] = counts.get(tool_name, 0) + 1
    return counts if observed else None


def parse_output(output: str, server: str = SERVER) -> tuple[dict[str, object], dict[str, int] | None]:
    """Parse Claude JSON or stream-json, counting distinct observed MCP tool uses of `server` when available."""
    events = _load_events(output)
    results = _result_events(events)
    return results[-1], _count_tool_uses(events, server)


def _check_adapter_result_fields(data: dict[str, object]) -> None:
    if not isinstance(data.get("result"), str) and not (isinstance(data.get("error"), str) and data["error"]):
        raise ValueError("adapter response must include a result string or nonempty error string")
    if data.get("error") is not None and not (isinstance(data["error"], str) and data["error"]):
        raise ValueError("error must be a nonempty string or null")
    if not valid_tool_call_counts(data.get("mcp_tool_calls")):
        raise ValueError("mcp_tool_calls must be a mapping of tool names to nonnegative integer counts")


def _check_adapter_metrics(data: dict[str, object]) -> None:
    usage = data.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise ValueError("usage must be an object or null")
    for key in ("num_turns", "duration_ms", "total_cost_usd"):
        if data.get(key) is not None and number(data[key]) is None:
            raise ValueError(f"{key} must be a nonnegative finite number or null")
    for key, value in (usage or {}).items():
        if value is not None and number(value) is None:
            raise ValueError(f"usage.{key} must be a nonnegative finite number or null")


def parse_adapter_response(stdout: str) -> dict[str, object]:
    """Decode and validate an adapter's JSON reply; raises ValueError/JSONDecodeError when malformed."""
    data = json.loads(stdout, parse_constant=reject_constant)
    if not isinstance(data, dict):
        raise ValueError("adapter response must be an object")
    _check_adapter_result_fields(data)
    _check_adapter_metrics(data)
    return data


def tag(text: str, name: str) -> str:
    m = re.findall(rf"<{name}>(.*?)</{name}>", text, re.DOTALL)
    return m[-1].strip() if m else ""


def record_run(n: int, repeat: int, phase: float | None, question: str, expected: str,
               res: dict[str, object]) -> dict[str, object]:
    error = res.get("error")
    text = res.get("result") if isinstance(res.get("result"), str) and not error else ""
    got = tag(text, "response")
    usage = res.get("usage") if isinstance(res.get("usage"), dict) else {}
    return {
        "question_number": n, "repeat": repeat, "phase_seconds": phase,
        "question": question, "expected": expected, "answer": got, "correct": not error and got == expected,
        "error": error, "summary": tag(text, "summary"), "feedback": tag(text, "feedback"),
        "reported_models": sorted(res["modelUsage"]) if isinstance(res.get("modelUsage"), dict) else None,
        "num_turns": number(res.get("num_turns")),
        "mcp_tool_calls": res.get("mcp_tool_calls"),
        "wall_time_s": number(res.get("wall_time_s")),
        "duration_ms": number(res.get("duration_ms")),
        "total_cost_usd": number(res.get("total_cost_usd")),
        "usage": {key: number(usage.get(key)) for key in USAGE_KEYS},
    }


def aggregate(records: list[dict[str, object]]) -> dict[str, object]:
    def total(values: list[float | int | None]) -> float | int | None:
        return sum(values) if all(value is not None for value in values) else None

    return {
        "correct": sum(record["correct"] for record in records),
        "runs": len(records),
        "total_cost_usd": total([record["total_cost_usd"] for record in records]),
        "wall_time_s": total([record["wall_time_s"] for record in records]),
        "mcp_tool_calls": total([
            sum(record["mcp_tool_calls"].values()) if record["mcp_tool_calls"] is not None else None
            for record in records
        ]),
        "usage": {key: total([record["usage"][key] for record in records]) for key in USAGE_KEYS},
    }


def render_report(records: list[dict[str, object]], totals: dict[str, object],
                  client: str = "Claude Code headless") -> str:
    cost = "unavailable" if totals["total_cost_usd"] is None else f"${totals['total_cost_usd']:.3f}"
    calls = totals["mcp_tool_calls"] if totals["mcp_tool_calls"] is not None else "unavailable"
    lines = [f"# NT4 evaluation ({client})", "",
             f"Accuracy: {totals['correct']}/{totals['runs']}  cost: {cost}  MCP tool calls: {calls}", ""]
    for r in records:
        tool_calls = sum(r["mcp_tool_calls"].values()) if r["mcp_tool_calls"] is not None else "unavailable"
        lines += [
            f"## Q{r['question_number']}: {'PASS' if r['correct'] else 'FAIL'}",
            f"**Question:** {r['question']}",
            f"**Expected:** `{r['expected']}`  **Got:** `{r['answer']}`",
            f"repeat={r['repeat']} phase_s={r['phase_seconds']} turns={r['num_turns']} "
            f"MCP_calls={tool_calls} wall_s={r['wall_time_s']} cost_usd={r['total_cost_usd']}",
            f"usage={r['usage']}" + (f" ERROR: {r['error']}" if r["error"] else ""),
            "", f"**Summary:** {r['summary']}", "", f"**Feedback:** {r['feedback']}", "",
        ]
    return "\n".join(lines)

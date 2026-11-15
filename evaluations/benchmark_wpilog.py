"""Model-free payload benchmark for the log server (`frc-mcp wpilog`): how many tokens does it put in front of a model,
compared with the obvious alternative (printing the rows a script would read from the log)? Free to run and
deterministic: it uses the practice log from `fake_log.py`.

    uv run --project plugins/frc python evaluations/benchmark_wpilog.py [--json out.json]

Tokens are estimated as characters / 4, so read the *ratios*, not the absolute numbers.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import fake_log
from benchmark_naive import tokens
from eval_common import atomic_write_text
from fastmcp import Client

from frc_mcp_wpilog.logfile import LogFile
from frc_mcp_wpilog.spec import SPEC
from frc_mcp_wpilog.store import logs

mcp, _ = SPEC.load()

ELEVATOR, BATTERY, ARM, SPEEDS = "/Fake/Elevator/Current", "/Fake/Battery/Voltage", "/Fake/Arm/Angle", "/Fake/Drive/Speeds"


def naive_rows(log: LogFile, names: list[str]) -> str:
    """What a hand-written script prints for a topic: every update as [time_us, value] (struct bytes as hex)."""
    out: dict[str, list[list[object]]] = {}
    for name, rows in log.rows(names).items():
        out[name] = [[t, v.hex() if isinstance(v, bytes) else v] for t, v in rows]
    return json.dumps(out)


def naive_topics(log: LogFile) -> str:
    return json.dumps([{"name": t.name, "type": t.log_type, "updates": t.count} for t in sorted(log.topics.values(), key=lambda t: t.name)])


def scenarios(log: LogFile) -> list[dict[str, object]]:
    strings = [t.name for t in log.topics.values() if t.type_str == "string"]
    return [
        {"name": "Open the log (overview)", "call": ("wpilog_open", {"path": "practice.wpilog"}), "naive": naive_topics(log)},
        {"name": "Topics one level deep", "call": ("wpilog_topics", {"depth": 1}), "naive": naive_topics(log)},
        {"name": "When did the elevator draw over 40 A?", "call": ("wpilog_when", {"topic": ELEVATOR, "op": ">", "value": 40, "align": "auto"}),
         "naive": naive_rows(log, [ELEVATOR])},
        {"name": "How steady is the drive speed (vx percentiles)?",
         "call": ("wpilog_query", {"topics": [SPEEDS], "reduce": "percentiles", "fields": ["vx"], "align": "auto"}),
         "naive": naive_rows(log, [SPEEDS])},
        {"name": "Arm angle and battery over the match (summary)", "call": ("wpilog_query", {"topics": [ARM, BATTERY], "reduce": "summary"}),
         "naive": naive_rows(log, [ARM, BATTERY])},
        {"name": "What errors and warnings happened?", "call": ("wpilog_text", {"name_contains": "e"}), "naive": naive_rows(log, strings)},
    ]


async def overhead() -> dict[str, int]:
    async with Client(mcp) as c:
        listed = await c.list_tools()
        blob = json.dumps([{"n": t.name, "d": t.description, "s": t.input_schema} for t in listed])
        return {"tools": len(listed), "schema_and_instruction_tokens": tokens(blob + (mcp.instructions or ""))}


def prepare(folder: Path) -> LogFile:
    """Step 1: the fixture on disk, the folder the server may read from, and its opened log."""
    fake_log.build().save(folder / "practice.wpilog")
    os.environ["WPILOG_DIR"] = str(folder)
    return LogFile(folder / "practice.wpilog")


async def _run_scenario(c: Client, sc: dict[str, object]) -> dict[str, object]:
    """Step 2, one scenario: ask the tool, then price its reply against the naive row dump."""
    name, args = sc["call"]  # type: ignore[misc]
    reply = (await c.call_tool(name, args)).content[0].text
    mcp_tokens, naive_tokens = tokens(reply), tokens(str(sc["naive"]))
    return {"scenario": sc["name"], "mcp_tokens": mcp_tokens, "naive_tokens": naive_tokens,
            "ratio": round(naive_tokens / max(mcp_tokens, 1), 1)}


async def run_all(folder: Path) -> tuple[dict[str, int], list[dict[str, object]]]:
    """Price every scenario against its naive dump, then add the fixed per-session cost."""
    opened = prepare(folder)
    async with Client(mcp) as c:
        results = [await _run_scenario(c, sc) for sc in scenarios(opened)]
    logs.reset()  # the store's own cleanup, so the next run starts from nothing
    return await overhead(), results


def render(over: dict[str, int], results: list[dict[str, object]]) -> str:
    lines = [f"Fixed per-session cost: {over['tools']} tools, ~{over['schema_and_instruction_tokens']} tokens of schema + instructions\n",
             "| Scenario (practice log) | MCP tokens | Naive tokens | Naive / MCP |", "|---|---:|---:|---:|"]
    lines += [f"| {r['scenario']} | {r['mcp_tokens']} | {r['naive_tokens']} | {r['ratio']}x |" for r in results]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Token cost of the log server's replies on the practice log.")
    ap.add_argument("--json", help="also write the results to this file")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="fake-log-bench-") as tmp:
        over, results = asyncio.run(run_all(Path(tmp)))
    sys.stdout.write(render(over, results) + "\n")
    if args.json:
        atomic_write_text(Path(args.json), json.dumps({"overhead": over, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "naive_rows", "naive_topics", "render", "run_all", "scenarios"]

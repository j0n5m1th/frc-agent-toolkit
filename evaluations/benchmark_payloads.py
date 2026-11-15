"""Model-free payload benchmark: how many tokens does the server put in front of a model, compared with the
obvious alternative (dumping what ntcore holds)? Free to run and deterministic on the fake robot.

    uv run --project plugins/frc python evaluations/benchmark_payloads.py [--sim-port 5810] [--json out.json]

Tokens are estimated as characters / 4 (a rough rule for English/JSON; real tokenizers differ by +-30%), so read
the *ratios*, not the absolute numbers. Scenarios run against a private fake robot started on --port; when a
WPILib simulation answers on --sim-port, the same questions also run against it.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import ntcore
from benchmark_naive import naive_dump, naive_recording, tokens
from eval_common import atomic_write_text, fake_robot_process
from fastmcp import Client

from frc_mcp_nt4.session import session
from frc_mcp_nt4.spec import SPEC

mcp, _ = SPEC.load()

DEFAULT_FAKE_PORT, DEFAULT_SIM_PORT = 5841, 5810
DEFAULT_WATCH_S = 12.0
CONNECT_TIMEOUT_S = 10
CONNECT_POLL_S = 0.05
TOPIC_SETTLE_S = 1.5  # time for the raw client's subscription to receive every topic and value
ROBOT_STARTUP_S = 2
CAPTURE_MAX_S = 120
SPIKE_THRESHOLD_A = 20


async def tool(c: Client, name: str, args: dict[str, object]) -> str:
    return (await c.call_tool(name, args)).content[0].text


async def run_target(label: str, host: str, port: int, scenarios: list[dict[str, object]],
                     watch_s: float) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    raw = ntcore.NetworkTableInstance.create()
    raw.startClient4("benchmark-naive")
    raw.setServer(host, port)
    deadline = time.time() + CONNECT_TIMEOUT_S
    while not raw.isConnected() and time.time() < deadline:
        time.sleep(CONNECT_POLL_S)
    if not raw.isConnected():
        return results
    everything = ntcore.MultiSubscriber(raw, [""])  # without a subscription the raw client sees no topics or values
    time.sleep(TOPIC_SETTLE_S)
    async with Client(mcp) as c:
        await c.call_tool("nt4_connection", {"host": host, "port": port})
        for sc in scenarios:
            if sc.get("sim_only") and label != "sim":
                continue
            if sc.get("fake_only") and label != "fake":
                continue
            kind = sc["kind"]
            if kind == "static":
                mcp_replies = [await tool(c, t, a) for t, a in sc["calls"]]
                naive = naive_dump(raw, sc.get("naive_names"))
            else:  # timed: record the same window both ways at once
                start = await tool(c, "nt4_capture_start", {"topics": sc["topics"], "all_changes": True, "duration_s": CAPTURE_MAX_S})
                cid = json.loads(start)["id"]
                naive = await asyncio.to_thread(naive_recording, raw, sc["topics"], watch_s)
                mcp_replies = [start] + [await tool(c, "nt4_capture_query", {"capture_id": cid, **q}) for q in sc["queries"]]
                await tool(c, "nt4_capture_query", {"capture_id": cid, "reduce": "latest"})
                await tool(c, "nt4_capture_stop", {"capture_id": cid, "discard": True})
            m = sum(tokens(r) for r in mcp_replies)
            n = tokens(naive)
            results.append({"target": label, "scenario": sc["name"], "mcp_tokens": m, "naive_tokens": n,
                            "ratio": round(n / m, 1) if m else None})
    del everything
    raw.stopClient()
    return results


def scenarios(watch_s: float) -> list[dict[str, object]]:
    return [
        {"name": "Discover topics (depth-1 overview)", "kind": "static",
         "calls": [("nt4_list_topics", {"depth": 1})]},
        {"name": "Read 3 current values", "kind": "static",
         "calls": [("nt4_get_values", {"topics": ["/Fake/Arm/Angle", "/Fake/Match/Mode", "/Fake/Elevator/Current"]})],
         "naive_names": ["/Fake/Arm/Angle", "/Fake/Match/Mode", "/Fake/Elevator/Current"], "fake_only": True},
        {"name": f"Elevator spikes over {watch_s:.0f} s (edges + durations)", "kind": "timed", "fake_only": True,
         "topics": ["/Fake/Elevator/Current"],
         "queries": [{"reduce": "edges", "threshold": SPIKE_THRESHOLD_A}, {"reduce": "durations"}]},
        {"name": f"Arm angle + mode over {watch_s:.0f} s (summary)", "kind": "timed", "fake_only": True,
         "topics": ["/Fake/Arm/Angle", "/Fake/Match/Mode"], "queries": [{"reduce": "summary"}]},
        {"name": f"Drive pose + speeds over {watch_s:.0f} s (summary)", "kind": "timed", "sim_only": True,
         "topics": ["/DriveState/Pose", "/DriveState/Speeds"],
         "queries": [{"reduce": "summary", "fields": ["translation.x", "translation.y", "vx"]}]},
    ]


async def overhead() -> dict[str, int]:
    async with Client(mcp) as c:
        tools = await c.list_tools()
        blob = json.dumps([{"n": t.name, "d": t.description, "s": t.input_schema} for t in tools])
        return {"tools": len(tools), "schema_and_instruction_tokens": tokens(blob + (mcp.instructions or ""))}


def render_overhead(over: dict[str, int]) -> str:
    return (f"Fixed per-session cost: {over['tools']} tools, "
            f"~{over['schema_and_instruction_tokens']} tokens of schema + instructions\n")


def render_table(results: list[dict[str, object]]) -> str:
    lines = ["| Target | Scenario | MCP tokens | Naive tokens | Naive / MCP |", "|---|---|---:|---:|---:|"]
    lines += [f"| {r['target']} | {r['scenario']} | {r['mcp_tokens']} | {r['naive_tokens']} | {r['ratio']}x |"
              for r in results]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_FAKE_PORT)
    ap.add_argument("--sim-port", type=int, default=DEFAULT_SIM_PORT)
    ap.add_argument("--seconds", type=float, default=DEFAULT_WATCH_S)
    ap.add_argument("--json")
    return ap.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    results: list[dict[str, object]] = []
    with fake_robot_process(args.port):
        try:
            time.sleep(ROBOT_STARTUP_S)
            sc = scenarios(args.seconds)
            results += await run_target("fake", "127.0.0.1", args.port, sc, args.seconds)
            session.disconnect()
            results += await run_target("sim", "127.0.0.1", args.sim_port, sc, args.seconds)
        finally:
            session.disconnect()
    over = await overhead()
    sys.stdout.write(render_overhead(over) + "\n" + render_table(results) + "\n")
    if args.json:
        atomic_write_text(Path(args.json), json.dumps({"overhead": over, "results": results}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())

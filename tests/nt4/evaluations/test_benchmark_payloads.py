"""Pure-logic tests for evaluations/benchmark_payloads.py (no robot or model is started)."""

import json

import benchmark_naive as naive
import benchmark_payloads as bench
import ntcore


def test_tokens_estimate_is_chars_over_four():
    assert naive.tokens("") == 0
    assert naive.tokens("a" * 40) == 10
    assert naive.tokens("a" * 6) == 2  # rounds


def test_naive_dump_lists_topics_and_filters_by_name():
    inst = ntcore.NetworkTableInstance.create()
    try:
        pub_a = inst.getDoubleTopic("/A/x").publish()  # keep references: ntcore unpublishes on garbage collection
        pub_a.set(1.5)
        pub_b = inst.getStringTopic("/B/y").publish()
        pub_b.set("hi")
        everything = {row["name"]: row for row in json.loads(naive.naive_dump(inst))}
        assert set(everything) == {"/A/x", "/B/y"}
        assert everything["/A/x"]["type"] == "double" and everything["/A/x"]["value"] == 1.5
        only = json.loads(naive.naive_dump(inst, ["/B/y"]))
        assert [row["name"] for row in only] == ["/B/y"]
    finally:
        ntcore.NetworkTableInstance.destroy(inst)


def test_scenarios_cover_fake_and_sim_targets_with_the_watch_window():
    sc = bench.scenarios(12.0)
    assert {s["kind"] for s in sc} == {"static", "timed"}
    assert any(s.get("sim_only") for s in sc) and any(s.get("fake_only") for s in sc)
    assert all("12 s" in s["name"] for s in sc if s["kind"] == "timed")
    assert all(not (s.get("sim_only") and s.get("fake_only")) for s in sc)


def test_render_table_and_overhead():
    results = [{"target": "fake", "scenario": "S", "mcp_tokens": 5, "naive_tokens": 50, "ratio": 10.0},
               {"target": "sim", "scenario": "T", "mcp_tokens": 0, "naive_tokens": 7, "ratio": None}]
    lines = bench.render_table(results).splitlines()
    assert lines[0].startswith("| Target |") and lines[1].startswith("|---|")
    assert lines[2] == "| fake | S | 5 | 50 | 10.0x |"
    assert lines[3] == "| sim | T | 0 | 7 | Nonex |"
    text = bench.render_overhead({"tools": 7, "schema_and_instruction_tokens": 1234})
    assert "7 tools" in text and "~1234" in text and text.endswith("\n")


def test_parse_args_defaults_and_overrides():
    args = bench.parse_args([])
    assert (args.port, args.sim_port, args.seconds, args.json) == (5841, 5810, 12.0, None)
    args = bench.parse_args(["--port", "6000", "--sim-port", "6001", "--seconds", "3", "--json", "o.json"])
    assert (args.port, args.sim_port, args.seconds, args.json) == (6000, 6001, 3.0, "o.json")


async def test_overhead_reports_tool_count_and_schema_size():
    over = await bench.overhead()
    assert over["tools"] > 0 and over["schema_and_instruction_tokens"] > 0

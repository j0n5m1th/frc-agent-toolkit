"""The log server benchmark runs offline and shows the server answering in a small fraction of the raw rows."""

import asyncio

import benchmark_wpilog


def test_the_benchmark_runs_on_the_practice_log_and_the_server_wins_where_it_should(tmp_path):
    over, results = asyncio.run(benchmark_wpilog.run_all(tmp_path))
    assert over["tools"] == len(asyncio.run(_tool_names())) and over["schema_and_instruction_tokens"] > 500
    by_name = {r["scenario"]: r for r in results}
    assert len(results) == 6
    assert by_name["How steady is the drive speed (vx percentiles)?"]["ratio"] > 50
    assert by_name["When did the elevator draw over 40 A?"]["ratio"] > 20
    assert "| Scenario (practice log) |" in benchmark_wpilog.render(over, results)


async def _tool_names():
    from fastmcp import Client

    async with Client(benchmark_wpilog.mcp) as c:
        return [t.name for t in await c.list_tools()]


def test_the_benchmarks_answers_are_right_not_just_small(tmp_path):
    """The numbers the benchmark prices must be the true ones, worked out here from the raw rows by hand."""
    from frc_mcp_wpilog import queries, timeline
    from frc_mcp_wpilog.queries import QuerySpec, Range

    log = benchmark_wpilog.prepare(tmp_path)
    rows = log.rows([benchmark_wpilog.ELEVATOR])[benchmark_wpilog.ELEVATOR]
    origin, _ = timeline.origin_us(log, "auto")
    expected, opened = [], None
    for t, v in rows:  # the stretches over 40 A: a value holds until the next update
        if v > 40 and opened is None:
            opened = t
        elif v <= 40 and opened is not None:
            expected.append([(opened - origin) / 1000, (t - origin) / 1000])
            opened = None
    if opened is not None:  # still over 40 A at the last update: it ends with the log
        expected.append([(opened - origin) / 1000, (log.end_us - origin) / 1000])
    got = queries.when(log, benchmark_wpilog.ELEVATOR, ">", 40, None, Range("auto"), 20)
    assert expected and got["intervals_ms"] == [[round(s, 1), round(e, 1)] for s, e in expected]
    summary = queries.query(log, [benchmark_wpilog.BATTERY], QuerySpec(reduce="summary"), Range())["data"][benchmark_wpilog.BATTERY]
    values = [v for _, v in log.rows([benchmark_wpilog.BATTERY])[benchmark_wpilog.BATTERY]]
    assert summary["min"] == round(min(values), 4) and summary["max"] == round(max(values), 4) and summary["n"] == len(values)
    benchmark_wpilog.logs.reset()

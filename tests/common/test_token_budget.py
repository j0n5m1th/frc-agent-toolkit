"""What each server costs in tokens is checked in (`token_baseline.json`), so a bigger server is a deliberate choice.

A server's tool names, tool definitions and instructions may grow by at most 5% (and 20 tokens, so tiny servers do not flake) over the baseline, in both toolsets
and with the switches off (the plugin default) and on. Tokens are characters / 4. When growth is on purpose, regenerate the baseline with:

    uv run --project plugins/frc --group dev python tests/common/update_token_baseline.py
"""

import asyncio
import json

import pytest
from update_token_baseline import BASELINE, PARTS, measure_all, startup_total

GROWTH = 1.05
SLACK = 20
STARTUP_TARGET = 2500  # names + instructions + skill descriptions with every server on
REGENERATE = "uv run --project plugins/frc --group dev python tests/common/update_token_baseline.py"

BASE = json.loads(BASELINE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def now():
    return asyncio.run(measure_all())


def cases():
    return [(server, toolset, gates, part) for server, sets in BASE.items() for toolset, states in sets.items() for gates in states for part in PARTS]


@pytest.mark.parametrize(("server", "toolset", "gates", "part"), cases())
def test_a_server_stays_within_its_token_baseline(server, toolset, gates, part, now, capsys):
    old = BASE[server][toolset][gates][part]
    new = now[server][toolset][gates][part]
    limit = max(old * GROWTH, old + SLACK)
    assert new <= limit, (
        f"{server} ({toolset}, {gates}) {part}: {new} tokens, baseline {old} (limit {limit:.0f}). "
        f"If the growth is on purpose, regenerate the baseline with: {REGENERATE}"
    )
    if new < old * 0.9 and old - new > SLACK:
        with capsys.disabled():
            print(f"\nnote: {server} ({toolset}, {gates}) {part} shrank to {new} from {old}; you may regenerate the baseline: {REGENERATE}")


def test_every_server_is_in_the_baseline(now):
    assert {s: set(v) for s, v in now.items()} == {s: set(v) for s, v in BASE.items()}, f"servers or toolsets changed; regenerate the baseline: {REGENERATE}"


def test_all_servers_on_stays_under_the_startup_target(now):
    total = startup_total(now)
    assert total["total"] <= STARTUP_TARGET, f"startup cost with every server on is {total} tokens; the target is {STARTUP_TARGET}"

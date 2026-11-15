"""Tools behind an `*_ALLOW_*` switch are hidden while the switch is off, shown when it is on, and a bad value stops the server the way a bad toolset does."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from gate_cases import CASES, NT4, SYSTEMCORE

from frc_mcp_core.toolset import set_gate_visibility

ALL_ENV = {name for _spec, env, _tools, _one in CASES.values() for name in env}
KEPT_VISIBLE = {"systemcore_logs_prune", "systemcore_connect", "systemcore_disconnect"}  # their gate is conditional (a dry run, a Wi-Fi join), so they are never hidden


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEMCORE_ENV_FILE", str(tmp_path / "missing.env"))
    for name in ALL_ENV | {"NT4_TOOLSET", "VSCODE_TOOLSET", "SYSTEMCORE_TOOLSET", "JFR_TOOLSET", "CANIV_TOOLSET"}:
        monkeypatch.delenv(name, raising=False)
    yield
    for spec, *_ in CASES.values():  # the servers are shared by the whole run: leave every tool visible
        mcp, _ = spec.create()
        set_gate_visibility(mcp, dict.fromkeys(spec.gates, True), core=False)


async def names(mcp) -> set[str]:
    async with Client(mcp) as client:
        return {t.name for t in await client.list_tools()}


def serve_mcp(spec, monkeypatch):
    """The server `spec.serve()` would run, with stdio replaced by a recorder."""
    mcp, _ = spec.load()
    started: list[bool] = []
    monkeypatch.setattr(mcp, "run", lambda **kwargs: started.append(True))
    return mcp, started


@pytest.mark.parametrize("key", CASES)
async def test_switch_off_hides_the_tools_and_calling_one_fails(key, monkeypatch):
    spec, _env, hidden, _one = CASES[key]
    mcp, started = serve_mcp(spec, monkeypatch)
    assert hidden <= await names(mcp)  # `load()` alone hides nothing
    spec.serve()
    assert started == [True]
    listed = await names(mcp)
    assert not hidden & listed
    async with Client(mcp) as client:
        for tool in hidden:
            with pytest.raises(ToolError, match="(?i)unknown tool|not found|disabled"):
                await client.call_tool(tool, {})


@pytest.mark.parametrize("key", CASES)
async def test_switch_on_lists_the_tools_and_turning_it_off_hides_them_again(key, monkeypatch):
    spec, env, hidden, _one = CASES[key]
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    mcp, _started = serve_mcp(spec, monkeypatch)
    spec.serve()
    assert hidden <= await names(mcp)
    for name in env:
        monkeypatch.setenv(name, "0")
    spec.apply_gates()  # the same server, applied again
    assert not hidden & await names(mcp)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec.apply_gates()
    assert hidden <= await names(mcp)


@pytest.mark.parametrize("key", CASES)
def test_a_bad_switch_value_stops_the_server_with_the_usual_message(key, monkeypatch):
    spec, _env, _hidden, one = CASES[key]
    mcp, started = serve_mcp(spec, monkeypatch)
    monkeypatch.setenv(one, "maybe")
    with pytest.raises(SystemExit, match=f"{one} must be"):
        spec.serve()
    assert started == []


@pytest.mark.parametrize("key", ["vscode", "jfr"])
async def test_a_gated_tool_that_is_not_full_only_stays_visible_in_core_when_its_switch_is_on(key, monkeypatch):
    spec, env, hidden, _one = CASES[key]
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert spec.toolset is not None
    monkeypatch.setenv(spec.toolset.env_name, "core")
    mcp, _started = serve_mcp(spec, monkeypatch)
    spec.serve()
    assert hidden <= await names(mcp)


async def test_core_and_a_closed_gate_both_hide_a_tool_that_is_in_both(monkeypatch):
    monkeypatch.setenv("NT4_TOOLSET", "core")
    monkeypatch.setenv("NT4_ALLOW_WRITES", "1")
    monkeypatch.setenv("NT4_WRITE_PREFIXES", "/Test")
    monkeypatch.setenv("NT4_READ_ONLY", "0")
    mcp, _started = serve_mcp(NT4, monkeypatch)
    NT4.serve()
    assert not {"nt4_set_value", "nt4_capture_start"} & await names(mcp)  # core hides both kinds
    monkeypatch.setenv("NT4_TOOLSET", "full")
    _, apply_toolset = NT4.create()
    apply_toolset()
    NT4.apply_gates()
    assert {"nt4_set_value", "nt4_capture_start"} <= await names(mcp)


@pytest.mark.parametrize(("env", "visible"), [
    ({}, False),
    ({"NT4_ALLOW_WRITES": "1"}, False),  # no allowed prefix: every write is refused
    ({"NT4_ALLOW_WRITES": "1", "NT4_WRITE_PREFIXES": "/Test"}, True),
    ({"NT4_ALLOW_WRITES": "1", "NT4_WRITE_PREFIXES": "/Test", "NT4_READ_ONLY": "1"}, False),  # read-only wins
    ({"NT4_ALLOW_WRITES": "1", "NT4_WRITE_PREFIXES": "Test"}, False),  # a bad prefix list hides the tools, it does not stop the server
])
async def test_nt4_write_tools_show_only_when_a_write_could_succeed(monkeypatch, env, visible):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    mcp, _started = serve_mcp(NT4, monkeypatch)
    NT4.serve()
    assert ("nt4_set_value" in await names(mcp)) is visible


async def test_systemcore_keeps_the_prune_and_wifi_tools_visible(monkeypatch):
    mcp, _started = serve_mcp(SYSTEMCORE, monkeypatch)
    SYSTEMCORE.serve()
    assert await names(mcp) >= KEPT_VISIBLE


async def test_instructions_say_more_tools_exist(monkeypatch):
    for key in CASES:
        spec = CASES[key][0]
        mcp, _ = spec.load()
        assert "turned on" in (mcp.instructions or "") or "appear" in (mcp.instructions or ""), key

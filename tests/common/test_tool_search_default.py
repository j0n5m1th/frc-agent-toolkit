"""Without tool search the assistant loads every tool at once, so the default toolset is `core` then; an explicit `*_TOOLSET` always wins."""

import pytest

from frc_mcp_core.toolset import Toolset, env_toolset, tool_search_off


class Problem(Exception):
    pass


@pytest.mark.parametrize("value", ["false", "FALSE", " False ", "0", "no", "off", "Off"])
def test_tool_search_off_words(monkeypatch, value):
    monkeypatch.setenv("ENABLE_TOOL_SEARCH", value)
    assert tool_search_off()


@pytest.mark.parametrize("value", [None, "", "  ", "true", "1", "auto", "auto:10", "yes"])
def test_tool_search_on_or_unknown_keeps_full(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ENABLE_TOOL_SEARCH", raising=False)
    else:
        monkeypatch.setenv("ENABLE_TOOL_SEARCH", value)
    assert not tool_search_off()
    assert env_toolset("T_SET", Problem) == "full"


@pytest.mark.parametrize("setting", [None, "", "  "])
def test_unset_toolset_becomes_core_when_tool_search_is_off(monkeypatch, setting):
    monkeypatch.setenv("ENABLE_TOOL_SEARCH", "false")
    if setting is None:
        monkeypatch.delenv("T_SET", raising=False)
    else:
        monkeypatch.setenv("T_SET", setting)
    assert env_toolset("T_SET", Problem) == "core"
    assert Toolset("T_SET", Problem).current() == "core"
    assert Toolset("T_SET", Problem, getenv={}.get).current() == "core"  # a server's own settings layer does not change the default


@pytest.mark.parametrize("tool_search", [None, "false", "true"])
@pytest.mark.parametrize("setting", ["full", "core"])
def test_an_explicit_toolset_always_wins(monkeypatch, tool_search, setting):
    if tool_search is None:
        monkeypatch.delenv("ENABLE_TOOL_SEARCH", raising=False)
    else:
        monkeypatch.setenv("ENABLE_TOOL_SEARCH", tool_search)
    monkeypatch.setenv("T_SET", setting)
    assert env_toolset("T_SET", Problem) == setting


def test_a_bad_toolset_still_raises_when_tool_search_is_off(monkeypatch):
    monkeypatch.setenv("ENABLE_TOOL_SEARCH", "false")
    monkeypatch.setenv("T_SET", "tiny")
    with pytest.raises(Problem, match="T_SET must be 'full' or 'core'"):
        env_toolset("T_SET", Problem)

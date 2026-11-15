"""The shared environment readers: the environment by default, or another source (a server's settings file behind it)."""

import pytest

from frc_mcp_core.server_support import env_toolset
from frc_mcp_core.settings import Capability, env_flag, env_int, env_number, env_str
from frc_mcp_wpilib.errors import WPILibMCPError


class Problem(WPILibMCPError):
    pass


def test_env_str_strips_and_treats_blank_as_unset(monkeypatch):
    monkeypatch.setenv("T_NAME", "  value  ")
    assert env_str("T_NAME") == "value"
    monkeypatch.setenv("T_NAME", "   ")
    assert env_str("T_NAME") is None and env_str("T_NAME", "fallback") == "fallback"
    monkeypatch.delenv("T_NAME")
    assert env_str("T_NAME") is None


def test_readers_use_the_environment_by_default(monkeypatch):
    monkeypatch.setenv("T_FLAG", "yes")
    monkeypatch.setenv("T_NUM", "7")
    monkeypatch.setenv("T_SET", "core")
    assert env_flag("T_FLAG", False, Problem) is True and env_number("T_NUM", 1, 0, 10, Problem) == 7 and env_int("T_NUM", 1, 0, 10, Problem) == 7
    assert env_toolset("T_SET", Problem) == "core"
    monkeypatch.setenv("T_FLAG", "maybe")
    with pytest.raises(Problem, match="T_FLAG"):
        env_flag("T_FLAG", False, Problem)


def test_readers_can_take_another_source(monkeypatch):
    values = {"T_FLAG": "1", "T_NUM": "3", "T_SET": "core", "T_NAME": " x "}
    monkeypatch.delenv("T_FLAG", raising=False)
    assert env_flag("T_FLAG", False, Problem, getenv=values.get) is True
    assert env_number("T_NUM", 1, 0, 10, Problem, getenv=values.get) == 3 and env_toolset("T_SET", Problem, getenv=values.get) == "core"
    assert env_str("T_NAME", getenv=values.get) == "x" and env_str("T_NONE", "d", getenv=values.get) == "d"
    assert env_toolset("T_UNSET", Problem, getenv={}.get) == "full"


def test_a_capability_reads_through_its_own_source(monkeypatch):
    values: dict[str, str] = {}
    gate = Capability("T_ALLOW", "Doing it", Problem, getenv=values.get)
    monkeypatch.delenv("T_ALLOW", raising=False)
    assert not gate.enabled() and "T_ALLOW=1" in (gate.refusal() or "")
    values["T_ALLOW"] = "1"
    assert gate.enabled() and gate.refusal() is None
    plain = Capability("T_ALLOW2", "Doing it", Problem)
    monkeypatch.setenv("T_ALLOW2", "on")
    assert plain.enabled()

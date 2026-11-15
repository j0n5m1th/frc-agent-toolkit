"""The `full` and `core` toolsets: `core` hides the tools tagged `FULL_ONLY_TAG`, a smaller schema bill for every session."""

import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from fastmcp import FastMCP

from .settings import FALSE_WORDS, Capability, Getenv, env_str, environ

FULL_ONLY_TAG = "full-only"
GATED_TAG = "gated"  # on every tool that always refuses while its `*_ALLOW_*` switch is off: the tool is hidden until the switch is on
GATE_TAG_PREFIX = "gate:"  # plus the switch's environment variable name: which switch hides the tool
TOOL_SEARCH_ENV = "ENABLE_TOOL_SEARCH"  # Claude Code's switch: `false` means the assistant loads every tool up front, so a smaller default toolset saves tokens
ToolsetName = Literal["full", "core"]


def parse_toolset(value: str, env_name: str, error: type[Exception]) -> ToolsetName:
    """`value` as 'full' (also when empty) or 'core'; anything else raises `error` naming the environment variable."""
    normalized = value.strip().lower() or "full"
    if normalized == "core":
        return "core"
    if normalized == "full":
        return "full"
    raise error(f"{env_name} must be 'full' or 'core', not {normalized!r}.")


def tool_search_off() -> bool:
    """True when the real environment says `ENABLE_TOOL_SEARCH` is false (also 0, no, off): the assistant has no tool search."""
    return (environ(TOOL_SEARCH_ENV) or "").strip().lower() in FALSE_WORDS


def env_toolset(env_name: str, error: type[Exception], *, getenv: Getenv | None = None) -> ToolsetName:
    """The toolset named by environment variable `env_name`; `error` is raised for any other value.

    When it is unset or blank the default is 'full', or 'core' while `ENABLE_TOOL_SEARCH` is false. A value that is set always wins.
    """
    return parse_toolset(env_str(env_name, getenv=getenv) or ("core" if tool_search_off() else "full"), env_name, error)


_applied_gates: "weakref.WeakKeyDictionary[FastMCP, dict[str, bool]]" = weakref.WeakKeyDictionary()  # the gates last applied to a server


def set_toolset_visibility(server: FastMCP, core: bool) -> None:
    """Hide (core) or show (full) the FULL_ONLY_TAG tools on `server`. Repeating a call is harmless.

    Once `set_gate_visibility` has run on the server, its gates are applied again, so a tool whose switch is off stays hidden.
    """
    if (gates := _applied_gates.get(server)) is not None:
        set_gate_visibility(server, gates, core)
    elif core:
        server.disable(tags={FULL_ONLY_TAG})
    else:
        server.enable(tags={FULL_ONLY_TAG})


def gate_tag(env_name: str) -> str:
    """The tag naming one switch, e.g. `gate:JFR_ALLOW_RECORD`."""
    return GATE_TAG_PREFIX + env_name


def gated_by(capability: Capability) -> set[str]:
    """The tags for a tool that always refuses while `capability` is off: `tags={FULL_ONLY_TAG, *gated_by(config.deploy)}`."""
    return {GATED_TAG, gate_tag(capability.env_name)}


def capability_gates(*capabilities: Capability) -> dict[str, Callable[[], bool]]:
    """Gates for `ServerSpec.gates`: each switch's tag, mapped to the function that says whether it is on now."""
    return {gate_tag(c.env_name): c.enabled for c in capabilities}


def set_gate_visibility(server: FastMCP, gates: Mapping[str, bool], core: bool) -> None:
    """Show the tools whose gate (tag -> on) is on and hide the others, keeping the `core` toolset's hidden tools hidden.

    Visibility transforms stack and the later one wins, so this first shows everything either rule can hide, then hides again: the
    full-only tools when `core`, then the tools of every gate that is off. Repeating it with other values is harmless.
    """
    _applied_gates[server] = dict(gates)
    server.enable(tags={FULL_ONLY_TAG, *gates})
    if core:
        server.disable(tags={FULL_ONLY_TAG})
    if closed := {tag for tag, on in gates.items() if not on}:
        server.disable(tags=closed)


def forget_all_gates() -> None:
    """For tests: show every tool of every server a gate was applied to, and forget those gates, so one test's gates never reach the next (a server object lives as long as its module)."""
    for server, gates in list(_applied_gates.items()):
        server.enable(tags={FULL_ONLY_TAG, *gates})
    _applied_gates.clear()


@dataclass(frozen=True)
class Toolset:
    """One server's `*_TOOLSET` setting: read it now, or check a value."""

    env_name: str
    error: type[Exception]
    getenv: Getenv | None = None  # a server that layers a settings file behind the environment passes its own reader

    def current(self) -> ToolsetName:
        """The setting's value now ('full' when unset, or 'core' while ENABLE_TOOL_SEARCH is false); read on every call."""
        return env_toolset(self.env_name, self.error, getenv=self.getenv)

    def parse(self, value: str) -> ToolsetName:
        return parse_toolset(value, self.env_name, self.error)

"""Setting a server up: the FastMCP instance with lean schemas and the core/full switch. `ServerSpec` (spec.py) calls these."""

from collections.abc import Callable
from typing import Any, Protocol

from fastmcp import FastMCP

from .learn import LearnTips
from .tools import SlimToolList
from .toolset import Toolset, ToolsetName, set_toolset_visibility


class ApplyToolset(Protocol):
    def __call__(self, value: str | None = None, /) -> ToolsetName: ...


def new_server(name: str, instructions: str, *, slim: SlimToolList | None = None, **options: Any) -> FastMCP:
    """A FastMCP instance with the lean-schema middleware: for a server with no core/full switch. `options` go to FastMCP (for example `lifespan`).

    Error details are masked (`mask_error_details`): only the text of a `ToolError` (what `tool_errors` makes of the repository's errors) reaches the model.
    """
    options.setdefault("mask_error_details", True)
    mcp = FastMCP(name, instructions=instructions, **options)
    mcp.add_middleware(LearnTips())  # adds a tip to a reply only while learning mode is on
    mcp.add_middleware(slim or SlimToolList())
    return mcp


def create_server(
    name: str,
    instructions: str | Callable[[bool], str],
    toolset: Toolset,
    *,
    slim: SlimToolList | None = None,
    on_toolset: Callable[[bool], None] | None = None,
    **options: Any,
) -> tuple[FastMCP, ApplyToolset]:
    """The server's FastMCP instance, with the lean-schema middleware, and its `apply_toolset`.
    `instructions` is a sentence, or a function of `core` when they change with the toolset. `slim` replaces `SlimToolList`;
    `on_toolset(core)` runs on every apply; `options` go to FastMCP (for example `lifespan`).
    `apply_toolset(value=None)` sets the toolset and returns its name (default: the setting, read now); an invalid value raises
    the server's error before anything changes. Repeating it is harmless, and 'full' after 'core' shows the hidden tools again.
    """
    core = toolset.current() == "core" if callable(instructions) or on_toolset else False
    mcp = new_server(name, instructions(core) if callable(instructions) else instructions, slim=slim, **options)
    if on_toolset:
        on_toolset(core)

    def apply_toolset(value: str | None = None) -> ToolsetName:
        chosen = toolset.current() if value is None else toolset.parse(value)
        is_core = chosen == "core"
        set_toolset_visibility(mcp, is_core)
        if callable(instructions):
            mcp.instructions = instructions(is_core)
        if on_toolset:
            on_toolset(is_core)
        return chosen

    return mcp, apply_toolset

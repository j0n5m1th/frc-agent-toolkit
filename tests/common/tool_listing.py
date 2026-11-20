"""Every tool of every server, listed once for the tests that check names against the real servers."""

from fastmcp import Client

from frc_mcp_core import command
from frc_mcp_core.toolset import set_gate_visibility

_found: dict[str, set[str]] = {}  # listed once per test run: starting every server takes seconds, and callers only read it


async def all_tools() -> dict[str, set[str]]:
    """Tool name -> tags, over every server with every switch on and the full toolset. Visibility is left all-on, as `spec.load()` hands it out."""
    if _found:
        return _found
    found = _found
    for key in command.server_keys():
        spec = command.load_spec(key)
        mcp, _ = spec.load()
        set_gate_visibility(mcp, dict.fromkeys(spec.gates, True), core=False)
        async with Client(mcp) as client:
            for tool in await client.list_tools():
                found[tool.name] = set(tool.meta.get("fastmcp", {}).get("tags", []) if tool.meta else [])
    return found

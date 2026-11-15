"""Shared tool plumbing: error conversion and the lean advertised schemas."""

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_core.server_support import tool_errors
from frc_mcp_wpilib.errors import WPILibMCPError


async def test_fail_keeps_message_verbatim_as_tool_error():
    @tool_errors
    async def tool() -> None:
        raise WPILibMCPError("Not connected.")

    with pytest.raises(ToolError) as caught:
        await tool()
    assert str(caught.value) == "Not connected."


async def test_tool_errors_turns_the_shared_error_into_a_tool_error():
    @tool_errors
    async def tool() -> None:
        raise WPILibMCPError("nope")

    with pytest.raises(ToolError, match="nope"):
        await tool()


async def test_both_servers_error_types_become_tool_errors_through_the_shared_wrapper():
    from frc_mcp_nt4.errors import NT4Error
    from frc_mcp_wpilog.errors import WPILogError

    for error in (NT4Error, WPILogError):
        @tool_errors
        async def tool(error=error) -> None:
            raise error("fix it like this")

        with pytest.raises(ToolError, match="fix it like this"):
            await tool()

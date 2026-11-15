"""What a failing tool says to the model: text that can always be written, OS errors with a kind and a reason but no path, the rest masked."""

import asyncio
import errno
import socket
import sys
import textwrap

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

from frc_mcp_core.errors import FrcMcpError
from frc_mcp_core.server import new_server
from frc_mcp_core.server_support import tool_errors

LOST = "bad \udcff.wpilog is too big"


def make_server():
    mcp = new_server("t", "text")

    @mcp.tool
    @tool_errors
    async def fail(kind: str) -> str:
        if kind == "surrogate":
            raise FrcMcpError(LOST)
        if kind == "permission":
            raise PermissionError(errno.EACCES, "Permission denied", "C:/Users/student/secret/log.wpilog")
        if kind == "missing":
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", "/home/student/robot/log.wpilog")
        if kind == "timeout":
            raise TimeoutError()
        if kind == "asyncio-timeout":
            await asyncio.wait_for(asyncio.sleep(5), 0.01)
        if kind == "refused":
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
        if kind == "dns":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        if kind == "other-os":
            raise OSError(errno.ENOSPC, "No space left on device", "D:/logs/out.wpilog")
        if kind == "unknown":
            raise RuntimeError("secret pw=hunter2 /home/u/.ssh/id")
        return "ok"

    @mcp.tool
    async def ok() -> str:
        return "still alive"

    return mcp


async def error_text(client: Client, kind: str) -> str:
    with pytest.raises(ToolError) as caught:
        await client.call_tool("fail", {"kind": kind})
    return str(caught.value)


async def test_a_lone_surrogate_in_an_error_is_escaped_so_the_message_can_be_written():
    async with Client(make_server()) as client:
        text = await error_text(client, "surrogate")
        text.encode("utf-8")
        assert "bad \\udcff.wpilog is too big" in text


@pytest.mark.parametrize(
    ("kind", "words"),
    [
        ("permission", ("PermissionError", "Permission denied", "Close the program")),
        ("missing", ("FileNotFoundError", "No such file or directory", "moved or deleted")),
        ("timeout", ("TimeoutError", "timed out")),
        ("asyncio-timeout", ("TimeoutError", "timed out")),
        ("refused", ("ConnectionRefusedError", "Connection refused")),
        ("dns", ("gaierror", "Name or service not known")),
        ("other-os", ("OSError", "No space left on device")),
    ],
)
async def test_an_os_error_tells_the_student_its_kind_and_reason_but_names_no_file(kind, words):
    async with Client(make_server()) as client:
        text = await error_text(client, kind)
    for word in words:
        assert word in text
    for path_part in ("student", "secret", "log.wpilog", "out.wpilog", "C:/", "D:/", "/home"):
        assert path_part not in text


async def test_an_unknown_exception_stays_masked():
    async with Client(make_server()) as client:
        text = await error_text(client, "unknown")
    assert "hunter2" not in text and ".ssh" not in text and "RuntimeError" not in text


async def test_a_surrogate_inside_an_os_error_reason_is_escaped_too():
    mcp = new_server("t", "text")

    @mcp.tool
    @tool_errors
    async def fail() -> str:
        raise OSError(5, "reason \udcff")

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("fail", {})
    str(caught.value).encode("utf-8")


SERVER = textwrap.dedent(
    """
    from frc_mcp_core.errors import FrcMcpError
    from frc_mcp_core.server import new_server
    from frc_mcp_core.server_support import tool_errors

    mcp = new_server("t", "text")

    @mcp.tool
    @tool_errors
    async def fail() -> str:
        raise FrcMcpError("bad \\udcff.wpilog is too big")

    @mcp.tool
    async def ok() -> str:
        return "still alive"

    mcp.run(show_banner=False)
    """
)


async def test_over_real_stdio_a_surrogate_in_an_error_does_not_end_the_server(tmp_path):
    script = tmp_path / "surrogate_server.py"
    script.write_text(SERVER, encoding="utf-8", newline="\n")
    async with Client(StdioTransport(sys.executable, [str(script)])) as client:
        with pytest.raises(ToolError) as caught:
            await asyncio.wait_for(client.call_tool("fail", {}), 30)
        assert "\\udcff" in str(caught.value)
        result = await asyncio.wait_for(client.call_tool("ok", {}), 30)  # the server is still there
        assert "still alive" in str(result)

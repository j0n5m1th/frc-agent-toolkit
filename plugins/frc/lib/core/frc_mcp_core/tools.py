"""What every tool needs: its server's errors turned into tool errors, and lean advertised schemas."""

import functools
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, TypedDict

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext
from fastmcp.tools import Tool

from .reply import encodable
from .schema_trim import slim_schema


class ToolErrors(Protocol):
    """The decorator `tool_errors_for` makes: it keeps the tool's signature and docstring."""

    def __call__[**P, R](self, fn: Callable[P, Awaitable[R]], /) -> Callable[P, Awaitable[R]]: ...


def os_error_message(e: OSError) -> str:
    """A short plain message for an operating-system error: its kind and its `strerror`, never a file name or path (the model may be told a path is none of its business)."""
    kind = type(e).__name__
    why = " ".join(str(e.strerror or "").split())[:100]
    detail = f"{kind}: {why}" if why else kind
    if isinstance(e, PermissionError):
        return f"The file is busy or not allowed ({detail}). Close the program using it and try again."
    if isinstance(e, FileNotFoundError):
        return f"A file or folder was not found ({detail}). Check the name, and that it was not moved or deleted."
    if isinstance(e, TimeoutError):
        return f"The operation timed out ({detail}). Check the connection or device, then try again."
    if isinstance(e, ConnectionError):
        return f"The connection failed ({detail}). Check that the other side is running and reachable, then try again."
    return f"The operating system refused the request ({detail}). Check the file, folder or device, then try again."


def tool_errors_for(*errors: type[Exception]) -> ToolErrors:
    """A decorator for under `@mcp.tool`: an exception of one of `errors` becomes a ToolError, whose message reaches the model as written.

    An `OSError` (a locked or missing file, a timeout, a refused connection) becomes a short message with its kind and reason but no path.
    Any other exception is masked by the server (`new_server` sets `mask_error_details`). Text that UTF-8 cannot encode (a file name with a
    lone surrogate) is escaped, because a message that cannot be written would end the whole server.
    """
    def decorate[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await fn(*args, **kwargs)
            except errors as e:
                raise ToolError(encodable(str(e))) from e
            except OSError as e:
                raise ToolError(encodable(os_error_message(e))) from e
        return wrapper
    return decorate


class ToolTuning(TypedDict, total=False):
    """The `tool.model_copy` update bag a `SlimToolList.tune` hook may fill in."""

    parameters: dict[str, Any]
    description: str


class SlimToolList(Middleware):
    """Advertise leaner schemas than the ones used for validation. Subclasses may also shorten descriptions through `tune`."""

    def tune(self, tool: Tool, update: ToolTuning) -> None:
        """Hook: add to `update` (fields for `tool.model_copy`) to change more than the parameter schema."""

    async def on_list_tools(self, context: MiddlewareContext, call_next: CallNext) -> Sequence[Tool]:
        tools = await call_next(context)
        out = []
        for tool in tools:
            update: ToolTuning = {"parameters": slim_schema(tool.parameters)}
            self.tune(tool, update)
            out.append(tool.model_copy(update=update))
        return out

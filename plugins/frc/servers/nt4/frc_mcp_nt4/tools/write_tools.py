"""Publishing tools: nt4_set_value and nt4_release_publisher (both need the write opt-in and allowlist)."""

from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply, topic_err
from frc_mcp_core.toolset import gated_by
from frc_mcp_wpilib.values import shape

from ..access.publishing import PublishOptions, SetResult
from ..access.reading import ReadMiss
from ..app import FULL_ONLY_TAG, WRITE, ReadBackWaitS, mcp, tool_errors
from ..config import allow_writes
from ..session import session

_IMMUTABLE_WARNING = "topic is marked mutable=false by its publisher; it may ignore or overwrite this"
_CHOOSER_NOTE = "selected/value is the robot's echo; to change a Selectable chooser write selected/tune instead."


def _render_set(res: SetResult, expires_in_s: float | None) -> dict[str, Any]:
    """The nt4_set_value reply, minus the read-back extras. `expires_in_s` is the final publisher expiry (already
    resolved by `_set_reply`). Key order is part of the tool's output."""
    if res.type_str is None:  # properties-only change
        out: dict[str, Any] = {"topic": res.topic, "properties": list(res.properties)}
    else:
        out = {"topic": res.topic, "type": res.type_str, "sent": shape(res.written, res.type_str)}
        if res.default:
            out["default"] = True
        if res.timestamp_us is not None:
            out["timestamp_us"] = res.timestamp_us
        if res.properties:
            out["properties"] = list(res.properties)
        if expires_in_s is not None:
            out["publisher_expires_in_s"] = expires_in_s
        if res.selectable_chooser:
            out["chooser_note"] = _CHOOSER_NOTE
    if res.immutable:
        out["warning"] = _IMMUTABLE_WARNING
    return out


async def _set_reply(res: SetResult, read_back: bool, read_back_wait_s: float) -> dict[str, Any]:
    """Render a SetResult to the full reply: the base fields, then default_applied and read_back when asked.
    The publisher expiry is resolved once, after the read-back wait, so it is the final value."""
    tail: dict[str, Any] = {}
    if res.default:
        tail["default_applied"] = await _default_applied(res.topic)
    if read_back:
        await _add_read_back(tail, res.topic, read_back_wait_s)

    expires = res.publisher_expires_in_s
    expired = False
    if read_back and expires is not None and res.type_str is not None:
        expires = session.publisher_expiry(res.topic)  # None: our publisher expired or was released
        expired = expires is None

    out = _render_set(res, expires)
    out.update(tail)  # tail keys never collide with base keys, so the order is preserved
    if expired:
        out["publisher_expired_or_released"] = True
    return out


async def _default_applied(topic: str) -> bool | None:
    """Did our weak `default` write become the value? A weak value carries timestamp 0 and never overrides one
    somebody set for real, so a real (timestamped) value after the grace period belongs to someone else."""
    observed = (await session.get_values([topic], timeout_s=1.0))[topic]
    if isinstance(observed, ReadMiss):
        return None  # could not read it back
    return observed.is_weak


async def _add_read_back(out: dict[str, Any], topic: str, wait_s: float) -> None:
    observed = (await session.get_values([topic], timeout_s=wait_s))[topic]
    if isinstance(observed, ReadMiss):
        out["read_back"] = topic_err(observed.hint)
    else:
        out["read_back"] = {
            "status": "observed", "type": observed.type_string, "v": observed.shaped(),
            "note": "Local client view, not server acknowledgement or proof the robot acted.",
        }


@mcp.tool(annotations={**WRITE, "title": "Publish value"}, output_schema=None, tags={FULL_ONLY_TAG, *gated_by(allow_writes)})
@tool_errors
async def nt4_set_value(
    topic: Annotated[str, Field(description="Topic name; created if new")],
    value: Annotated[Any, Field(description="JSON for the type: true, 3.14, 'text', [1,2]; hex for raw. Omit for a properties-only change")] = None,
    type_str: Annotated[str | None, Field(description="NT4 type; needed for new topics (see nt4://types)")] = None,
    default: Annotated[bool, Field(description="Weak write: never overrides a value someone else set")] = False,
    properties: Annotated[dict[str, Any] | None, Field(description="Properties to merge: persistent/retained/cached or custom JSON; null deletes. e.g. {'persistent': true}")] = None,
    publisher_ttl_s: Annotated[float | None, Field(strict=True, gt=0, le=3600, description="Seconds until our publisher is released")] = None,
    timestamp_us: Annotated[int | None, Field(strict=True, description="Sample time in microseconds on the server clock (server_time_us in status); default now")] = None,
    read_back: Annotated[bool, Field(description="Also read our local client's view (not proof the robot acted)")] = False,
    read_back_wait_s: ReadBackWaitS = 1.0,
) -> ToolResult:
    """Set a robot value and/or properties (the result echoes what you `sent`, not what the server holds). Needs explicit opt-in and an allowlist. The publisher is held until nt4_release_publisher/disconnect; retained/persistent topics outlive it."""
    async with session.operation():
        opts = PublishOptions(default=default, properties=properties, publisher_ttl_s=publisher_ttl_s, timestamp_us=timestamp_us)
        res = session.set_value(topic, value, type_str, opts)
        return reply(await _set_reply(res, read_back, read_back_wait_s))


@mcp.tool(annotations={**WRITE, "title": "Release publisher"}, output_schema=None, tags={FULL_ONLY_TAG, *gated_by(allow_writes)})
@tool_errors
async def nt4_release_publisher(
    topic: Annotated[str, Field(description="Topic whose publisher this client holds")],
) -> ToolResult:
    """Release our publisher for a topic (the server deletes the topic unless others publish it or it is retained/persistent). Needs the same opt-in and allowlist as nt4_set_value."""
    async with session.operation():
        return reply({"topic": topic, "released": session.unpublish(topic)})

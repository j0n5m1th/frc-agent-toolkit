"""The two single-shot capture tools: stop (flush and report) and watch (one-shot capture + query)."""

from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from ..app import (
    FULL_ONLY_TAG,
    LOCAL_STATE,
    AllChanges,
    AsPrefixes,
    CaptureId,
    Fields,
    MaxSamples,
    MaxTopics,
    RateHz,
    ReduceParam,
    Threshold,
    Topics,
    mcp,
    tool_errors,
)
from ..capture import CaptureSpec, make_spec
from ..session import session
from .reply import CAPTURE_BULK_KEYS


@mcp.tool(annotations={**LOCAL_STATE, "title": "Stop capture"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def nt4_capture_stop(
    capture_id: CaptureId,
    discard: Annotated[bool, Field(description="Also free the capture and its data; read what you need with nt4_capture_query first")] = False,
) -> ToolResult:
    """Stop a capture. Without `discard` it stays readable by nt4_capture_query; with it, the capture is freed."""
    async with session.operation(require_connection=False):
        cap = session.capture_stop(capture_id, discard=discard)
        info = {key: value for key, value in cap.info().items() if key not in ("topics", "rate_hz")}  # the caller knows them
        if cap.log_path:
            info["log_file"] = str(cap.log_path)  # where the recording ended up
        return reply({**info, "discarded": discard})


@mcp.tool(annotations={**LOCAL_STATE, "title": "Watch (one-shot capture)"}, output_schema=None)
@tool_errors
async def nt4_watch(
    topics: Topics,
    duration_s: Annotated[float, Field(gt=0, le=120, description="Seconds to record")] = 5.0,
    prefix: AsPrefixes = False,
    rate_hz: RateHz = 10,
    all_changes: AllChanges = False,
    reduce: ReduceParam = "summary",
    threshold: Threshold = None,
    fields: Fields = None,
    max_samples: MaxSamples = 20,
    max_topics: MaxTopics = 40,
) -> ToolResult:
    """Robot values for `duration_s` seconds: return one reduced result (`all` holds fields every topic shares), free everything. Use all_changes for events (updates may be lost). For triggers or windows use nt4_capture_start+query."""
    spec = CaptureSpec(topics=topics, prefix=prefix, rate_hz=rate_hz, duration_s=duration_s,
                       all_changes=all_changes)
    query = make_spec(topics=None, reduce=reduce, fields=fields, since_ms=None, until_ms=None,
                      max_samples=max_samples, threshold=threshold, max_topics=max_topics, events_after=None,
                      max_events=20, event_comparison=None, include_evidence=False, rel_trigger=False)
    async with session.operation():
        res = await session.watch_once(spec, query)
    return reply(res, CAPTURE_BULK_KEYS)

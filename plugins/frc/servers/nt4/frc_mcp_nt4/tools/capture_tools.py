"""Values over time: nt4_capture_start / nt4_capture_query / nt4_capture_stop (background captures) and nt4_watch (one-shot)."""

from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from ..app import (
    FULL_ONLY_TAG,
    LOCAL_STATE,
    READ_ONLY,
    AllChanges,
    AsPrefixes,
    CaptureId,
    CaptureWaitS,
    Fields,
    MaxSamples,
    MaxTopics,
    RateHz,
    ReduceParam,
    SinceMs,
    Threshold,
    Topics,
    UntilMs,
    mcp,
    tool_errors,
)
from ..capture import CaptureSpec, EventComparison, EventRule, Trigger, make_spec
from ..session import session
from .reply import CAPTURE_BULK_KEYS


@mcp.tool(annotations={**LOCAL_STATE, "title": "Start capture"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def nt4_capture_start(
    topics: Topics,
    prefix: AsPrefixes = False,
    rate_hz: RateHz = 10,
    duration_s: Annotated[float, Field(gt=0, le=3600, description="Auto-stop after (seconds)")] = 60,
    all_changes: AllChanges = False,
    log: Annotated[bool, Field(description="Also save updates to a .wpilog file (bounded) the log server can open")] = False,
    trigger: Annotated[Trigger | None, Field(description="Once this condition turns true (false->true), record after_s more seconds then stop; the lead-up stays buffered; topic is recorded too")] = None,
    event_rules: Annotated[list[EventRule] | None, Field(min_length=1, max_length=8, description="Observed-event inbox: rules record false->true crossings or value changes; bounded, may miss upstream losses")] = None,
) -> ToolResult:
    """Record robot values in the background; returns an id for nt4_capture_query. Only the named topics are subscribed."""
    spec = CaptureSpec(topics=topics, prefix=prefix, rate_hz=rate_hz, duration_s=duration_s,
                       all_changes=all_changes, trigger=trigger, event_rules=event_rules)
    async with session.operation():
        cap = session.capture_start(spec, log=log)
    out: dict[str, Any] = {"id": cap.id}
    if trigger:
        out["trigger"] = "armed"
    if cap.log_path:
        out["log_file"] = str(cap.log_path)
    if event_rules:
        out["event_rules"] = len(event_rules)
    return reply(out)


@mcp.tool(annotations={**READ_ONLY, "title": "Query capture"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def nt4_capture_query(
    capture_id: CaptureId,
    topics: Annotated[list[str] | None, Field(description="Subset (default all)")] = None,
    reduce: ReduceParam = "summary",
    fields: Fields = None,
    since_ms: SinceMs = None,
    until_ms: UntilMs = None,
    threshold: Threshold = None,
    max_samples: MaxSamples = 20,
    max_topics: MaxTopics = 40,
    wait_s: CaptureWaitS = 0,
    rel_trigger: Annotated[bool, Field(description="since/until relative to the trigger time (-1000 = 1 s before)")] = False,
    events_after: Annotated[int | None, Field(ge=0, description="Event cursor: 0, then events.next_after; re-read if events.revision changes; omit for counts only")] = None,
    max_events: Annotated[int, Field(ge=1, le=50, description="Max event records per page")] = 20,
    include_evidence: Annotated[bool, Field(description="Per-topic retained range, drop counts, last-update age")] = False,
    event_comparison: Annotated[EventComparison | None, Field(description="Pair each start-rule event with the next end-rule event; gaps make pairs unknown")] = None,
) -> ToolResult:
    """Capture results (reduced view); times are ms since capture start. Outages break edge/duration/change results. capture_rate_hz is the stored-sample rate, not the topic's own; mean is sample-weighted; unchanging topics are under `constants`; `all` holds fields every topic in `data` shares."""
    async with session.operation(require_connection=False):
        cap = await session.capture_wait(capture_id, timeout_s=wait_s)
        spec = make_spec(topics=topics, reduce=reduce, fields=fields, since_ms=since_ms, until_ms=until_ms,
                         max_samples=max_samples, threshold=threshold, max_topics=max_topics,
                         events_after=events_after, max_events=max_events, event_comparison=event_comparison,
                         include_evidence=include_evidence, rel_trigger=rel_trigger)
        return reply(cap.execute_query(spec), CAPTURE_BULK_KEYS)

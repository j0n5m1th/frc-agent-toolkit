"""Reading tools: current values, widget groups, and waiting for a condition."""

from dataclasses import replace
from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply, topic_err
from frc_mcp_wpilib import groups
from frc_mcp_wpilib.conditions import WaitOp
from frc_mcp_wpilib.values import DEFAULT_PRECISION, shape, type_field

from ..access.reading import ReadMiss, group_key
from ..app import (
    READ_ONLY,
    Also,
    Fields,
    IncludeAge,
    StructField,
    Topics,
    ValueWaitS,
    WaitConditionS,
    mcp,
    tool_errors,
)
from ..clock import local_now_us
from ..session import session
from .reply import ReplySpec, reply_entry


def _age_context(include_age: bool) -> tuple[int | None, bool] | None:
    """The age input `(now_us, synchronized)` that `reply_entry` turns into `last_update` via `ReplySpec.age`, or None when not asked for."""
    return (local_now_us(), session.clock_synchronized()) if include_age else None


@mcp.tool(annotations={**READ_ONLY, "title": "Get current values"}, output_schema=None)
@tool_errors
async def nt4_get_values(
    topics: Topics,
    wait_s: ValueWaitS = 2.0,
    fields: Fields = None,
    timestamps: Annotated[bool, Field(description="Include server timestamp (us)")] = False,
    include_age: IncludeAge = False,
) -> ToolResult:
    """Robot values now (one-shot). Structs decode to dotted fields; long arrays/strings truncate. err 'not announced' = no such topic; 'no value yet' = nothing published."""
    async with session.operation():
        got = await session.get_values(topics, timeout_s=wait_s)
        spec = ReplySpec(fields=fields, age=_age_context(include_age), timestamps=timestamps)
        out = {name: reply_entry(res, replace(spec, unit=session.topic_unit(name))) for name, res in got.items()}
        return reply(out, topics=len(out))


@mcp.tool(annotations={**READ_ONLY, "title": "Get widget groups"}, output_schema=None)
@tool_errors
async def nt4_get_widgets(
    bases: Annotated[list[str], Field(min_length=1, max_length=40, description="Widget base paths, e.g. '/Tunables/Auto Chooser'; each base plus its children is read as one widget")],
    wait_s: ValueWaitS = 2.0,
    fields: Fields = None,
    include_age: IncludeAge = False,
) -> ToolResult:
    """Dashboard widgets' current values, grouped. Choosers return {options, default, active, selected, current}; current is a candidate, not proof of the robot's choice."""
    async with session.operation():
        return reply(await _read_groups(bases, wait_s, fields, include_age), topics=len(bases))


async def _read_group(base: str, wait_s: float, fields: list[str] | None, include_age: bool) -> groups.ChooserReply | groups.PlainGroupReply | dict[str, str]:
    """One widget: `base` plus its children, built into the shape `groups.build_widget_group` recognizes."""
    names, truncated = session.expand(base)
    if not names:
        return topic_err("no topics under this path")
    got = await session.get_values(names, timeout_s=wait_s)
    spec = ReplySpec(fields=fields, age=_age_context(include_age))
    rel: dict[str, Any] = {}
    ages: dict[str, Any] = {}
    unavailable: dict[str, dict[str, str]] = {}
    for name, res in got.items():
        key = group_key(name, base)
        if isinstance(res, ReadMiss):
            unavailable[key] = reply_entry(res)
            continue
        rel[key] = res.shaped(fields)
        if spec.age is not None:
            ages[key] = res.age(*spec.age)
    return groups.build_widget_group(rel, truncated, unavailable, ages if include_age else None)


async def _read_groups(bases: list[str], wait_s: float, fields: list[str] | None, include_age: bool) -> dict[str, Any]:
    return {base: await _read_group(base, wait_s, fields, include_age) for base in bases}


@mcp.tool(annotations={**READ_ONLY, "title": "Wait for condition"}, output_schema=None)
@tool_errors
async def nt4_wait_for(
    topic: Annotated[str, Field(description="Exact name; may not exist yet")],
    op: Annotated[WaitOp, Field(description="Condition. 'changed' = differs from the value at call time (a topic with no value yet needs two distinct values); 'exists' = has a value")],
    value: Annotated[Any, Field(description="Comparison value (not needed for changed/exists)")] = None,
    field: StructField = None,
    wait_s: WaitConditionS = 10.0,
    also: Also = None,
) -> ToolResult:
    """Wait until an update meets the condition (and every `also` holds too), or timeout. Brief pulses can still be missed upstream."""
    async with session.operation():
        result = await session.wait_for(name=topic, op=op, target=value, field=field, timeout_s=wait_s, also=also or ())
        out: dict[str, Any] = {"met": result.met, "waited_s": round(result.waited_s, 2)}
        if result.raw is not None:
            out.update(type_field(result.type_str))
            out["v"] = shape(result.raw, result.type_str, [field] if field else None, DEFAULT_PRECISION)
        elif not result.met:
            out["note"] = "no value received; topic may not exist (nt4_list_topics)"
        if result.others:
            out["also"] = {name: shape(raw, type_str, None, DEFAULT_PRECISION) for name, (type_str, raw) in result.others.items()}
        return reply(out)

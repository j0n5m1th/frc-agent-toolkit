"""Topic tools: wpilog_topics lists what a log contains."""

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply, response_budget
from frc_mcp_wpilib.schema.state import fields_by_type
from frc_mcp_wpilib.tooling.topic_pages import collapse_topics, fit_page, list_header

from ..app import (
    READ_ONLY_LOCAL,
    Depth,
    IncludeHidden,
    LogId,
    NameContains,
    PageLimit,
    PageOffset,
    Prefix,
    StructFields,
    mcp,
    tool_errors,
)
from ..logfile import TopicFilter, serialized
from ..loglike import LogLike
from ..store import logs

TypeStr = Annotated[str | None, Field(description="Only this type, e.g. double, boolean, struct:Pose2d")]
Counts = Annotated[bool, Field(description="Show how many updates each topic has (props.n)")]


@dataclass(frozen=True)
class Listing:
    """What one page of the listing is about: which topics (`criteria`), which page, and how to shape the reply."""

    criteria: TopicFilter
    depth: int | None
    struct_fields: bool
    offset: int
    limit: int


@serialized
def _page(opened: LogLike, listing: Listing) -> dict[str, Any]:
    """One page of the topic listing. Holds the lock because struct_fields swaps the shared schema registries."""
    rows, hidden = opened.topic_rows(listing.criteria)
    entries = collapse_topics(rows, listing.criteria.prefix, listing.depth)
    budget = response_budget()
    base = list_header(len(rows), len(entries), hidden, budget)
    fields_of = None
    if listing.struct_fields:
        opened.activate_schemas()
        fields_of = fields_by_type
    return fit_page(base, entries, listing.offset, listing.limit, fields_of, budget)


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "List topics in a log"}, output_schema=None)
@tool_errors
async def wpilog_topics(
    prefix: Prefix = "",
    depth: Depth = None,
    name_contains: NameContains = None,
    type_str: TypeStr = None,
    counts: Counts = False,
    include_hidden: IncludeHidden = False,
    struct_fields: StructFields = False,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    log: LogId = None,
) -> ToolResult:
    """List log topics, sorted and size-bounded (depth=1 to explore): `topics` {topic: type}, `groups` {path: count}. `total` = matching topics, `total_items` = groups + topics, `next_offset` = pass as `offset` for the rest; `props` (sample count `n` with `counts`, plus the unit), `struct_fields` (with `struct_fields`), `hidden_matches` + `hidden_examples` or `hidden_examples_omitted` (with `include_hidden`). No values."""
    opened = logs.get(log)
    criteria = TopicFilter(prefix, name_contains, type_str, include_hidden, counts)
    listing = Listing(criteria, depth, struct_fields, offset, limit)
    page = await asyncio.to_thread(_page, opened, listing)
    return reply(page)

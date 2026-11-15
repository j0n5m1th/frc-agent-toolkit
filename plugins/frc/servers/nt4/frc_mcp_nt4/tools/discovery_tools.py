"""Discovery tools: nt4_list_topics (what exists) and nt4_inspect (who is connected, who publishes/subscribes what)."""

import asyncio
from typing import Annotated, Any, Literal, TypedDict

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply, response_budget
from frc_mcp_wpilib.tooling.topic_pages import collapse_topics, fit_page, list_header

from ..access.topic_listing import TopicFilter
from ..app import (
    READ_ONLY,
    Depth,
    IncludeHidden,
    MaxRows,
    NameContains,
    PageLimit,
    PageOffset,
    StructFields,
    mcp,
    tool_errors,
)
from ..errors import NT4Error
from ..session import session

MAX_INSPECT_TARGETS = 10


class InspectEntry(TypedDict, total=False):
    """One target's rows, or the in-band `err` when that target failed."""

    n: int
    rows: list[dict[str, Any]]
    err: str


class InspectReply(TypedDict, total=False):
    """The one `nt4_inspect` shape, whatever form `target` took: every answer is a `targets` map.

    `omitted_targets` appears only when a prefix expansion overflowed `MAX_INSPECT_TARGETS`."""

    what: str
    targets: dict[str, InspectEntry]
    omitted_targets: int


@mcp.tool(annotations={**READ_ONLY, "title": "List topics"}, output_schema=None)
@tool_errors
async def nt4_list_topics(
    prefix: Annotated[str, Field(description="Names starting with this, e.g. '/Tunables/'; '$' lists server meta-topics")] = "",
    depth: Depth = None,
    name_contains: NameContains = None,
    type_str: Annotated[str | None, Field(description="Only this NT4 type")] = None,
    flag: Annotated[Literal["persistent", "retained", "uncached"] | None, Field(description="Only topics with this property: persistent, retained, or uncached")] = None,
    include_hidden: IncludeHidden = False,
    all_props: Annotated[bool, Field(description="Also show custom properties")] = False,
    struct_fields: StructFields = False,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> ToolResult:
    """List topics, sorted and size-bounded (depth=1 to explore): `topics` {topic: type}, `groups` {path: count}. `total` = matching topics, `total_items` = groups + topics, `next_offset` = pass as `offset` for the rest; `props` (with `all_props`), `struct_fields` (with `struct_fields`), `hidden_matches` + `hidden_examples` or `hidden_examples_omitted` (with `include_hidden`). No values."""
    async with session.operation():
        await session.announce_meta(prefix)
        topics, hidden_names = session.list_topics(TopicFilter(prefix, name_contains, type_str, include_hidden=include_hidden, flag=flag, all_props=all_props))
    entries = collapse_topics(topics, prefix, depth)
    budget = response_budget()
    base = list_header(len(topics), len(entries), hidden_names, budget)
    return reply(fit_page(base, entries, offset, limit, session.field_names if struct_fields else None, budget))


# --- nt4_inspect: who is connected / who pub-subs what ---------------------
_META = {  # what -> (server meta-topic prefix, what the target is (None: no target))
    "clients": ("$clients", None),
    "subscribers": ("$sub$", "topic"),
    "publishers": ("$pub$", "topic"),
    "client_subs": ("$clientsub$", "client"),
    "client_pubs": ("$clientpub$", "client"),
    "server_subs": ("$serversub", None),
    "server_pubs": ("$serverpub", None),
}


def _is_our_client_name(name: str) -> bool:
    """The server names a client '<identity>@<n>'. Match that exactly: the identity itself may contain '@'."""
    identity = session.identity
    if not identity or not name.startswith(identity + "@"):
        return name == identity and bool(identity)
    return name[len(identity) + 1:].isdigit()


def _label_clients(row: dict[str, Any]) -> dict[str, Any]:
    """'' -> (server) (the robot program's own in-process publishers/subscribers); our connection -> (self).

    Open `dict[str, Any]` on purpose: a meta-topic row's keys depend on the meta-topic (`$clients`, `$sub$`, ...)."""
    out = dict(row)
    for key in ("client", "id"):
        name = out.get(key)
        if name == "":
            out[key] = "(server)"
        elif isinstance(name, str) and _is_our_client_name(name):
            out[key] = "(self)"
    return out


def _as_names(target: str | list[str] | None) -> list[str]:
    """`target` in the one form the rest of this module uses: a list of names, a bare str being a one-name list."""
    if isinstance(target, str):
        return [target]
    return list(target or [])


def _inspect_targets(names: list[str], kind: str) -> tuple[list[str], int]:
    """Names to inspect and how many were left out. A trailing `*` expands to the topics under that prefix."""
    found: list[str] = []
    for item in names:
        if not isinstance(item, str) or not item:
            raise NT4Error("`target` entries must be non-empty strings.")
        if item.endswith("*"):
            if kind != "topic":
                raise NT4Error("A trailing * only works for topic targets (subscribers/publishers).")
            found.extend(t["name"] for t in session.list_topics(TopicFilter(prefix=item[:-1]))[0])
        else:
            found.append(item)
    names = list(dict.fromkeys(found))
    if not names:
        raise NT4Error("No topics match that prefix.")
    return names[:MAX_INSPECT_TARGETS], max(0, len(names) - MAX_INSPECT_TARGETS)


async def _inspect_one(meta_topic: str, max_rows_per_target: int) -> InspectEntry:
    """One target: its rows, or the in-band `err` entry when that target failed."""
    try:
        rows = await session.inspect_meta(meta_topic)
    except NT4Error as e:
        return InspectEntry(err=str(e))  # the same err-only form the shared `topic_err` gives, typed at its source
    return {"n": len(rows), "rows": [_label_clients(r) for r in rows[:max_rows_per_target]]}


async def _inspect_many(what: str, prefix: str, names: list[str], omitted: int, max_rows_per_target: int) -> InspectReply:
    """One reply shape for every `target` form: a str is just a one-name list, no target is the "" name."""
    results = await asyncio.gather(*(_inspect_one(prefix + n, max_rows_per_target) for n in names))
    out: InspectReply = {"what": what, "targets": dict(zip(names, results, strict=True))}
    if omitted:
        out["omitted_targets"] = omitted
    return out


@mcp.tool(annotations={**READ_ONLY, "title": "Inspect clients/pub/sub"}, output_schema=None)
@tool_errors
async def nt4_inspect(
    what: Annotated[Literal["clients", "subscribers", "publishers", "client_subs", "client_pubs", "server_subs", "server_pubs"],
                    Field(description="clients = who is connected; subscribers/publishers = of topic `target`; client_subs/client_pubs = of client `target`")],
    target: (
        Annotated[str | None, Field(description="Topic name, or client name as listed by `clients`; a topic ending in * means every topic with that prefix")]
        | Annotated[list[str] | None, Field(max_length=MAX_INSPECT_TARGETS, description="Several names (at most 10, the rest reported as omitted_targets)")]
    ) = None,
    max_rows_per_target: MaxRows = 50,
) -> ToolResult:
    """Why isn't my value updating? Server meta-topics decoded (who publishes/subscribes, with what options). '(server)' = the robot program; '(self)' = this connection. `target` is required except for clients, server_subs, server_pubs."""
    prefix, kind = _META[what]
    if kind and not target:
        raise NT4Error(f"`target` ({kind} name) is required for what={what!r}.")
    if target and not kind:
        raise NT4Error(f"`target` is not used with what={what!r}.")
    async with session.operation():  # connects lazily, so topic expansion must happen inside it
        names, omitted = ([], 0) if target is None else _inspect_targets(_as_names(target), kind or "")
        if not names and kind is None:
            names = [""]  # the no-target kinds answer under the "" key, same map as a named target
        return reply(await _inspect_many(what, prefix, names, omitted, max_rows_per_target), bulk_keys=("targets",))

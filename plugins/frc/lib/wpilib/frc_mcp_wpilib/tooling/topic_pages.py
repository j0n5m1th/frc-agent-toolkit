"""Paged topic listings: collapse deep topics into `path/` groups, then fit one page to the size budget. Shared by
`nt4_list_topics` and `wpilog_topics`. A topic row is `{"name": ..., "type": ..., "props": {...}}`.
"""

from collections.abc import Callable, Iterable
from typing import Any, Literal, NamedTuple

from frc_mcp_core.reply import PAGE_NEXT_OFFSET, PAGE_TOTAL, fits

HIDDEN_EXAMPLES_MAX = 5  # name hidden matches when few (usually `.type` metadata topics)
FieldsOf = Callable[[Iterable[str]], dict[str, list[str]]]  # {type: field names} for the struct types in a page


class GroupEntry(NamedTuple):
    """A collapsed 'path/' page entry: the group key and how many topics it holds."""

    name: str
    tag: Literal["group"]
    total: int


class TopicEntry(NamedTuple):
    """A topic page entry: its name and the server's row ({name, type, props})."""

    name: str
    tag: Literal["topic"]
    data: dict[str, Any]


PageEntry = GroupEntry | TopicEntry  # `tag` is the discriminator: an int count, or the topic's row


def collapse_topics(topics: list[dict[str, Any]], prefix: str, depth: int | None) -> list[PageEntry]:
    """Sorted page entries: topics deeper than `depth` collapse into 'path/' groups (a lone topic stays listed)."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    leaves = []
    for t in topics:
        rest = t["name"][len(prefix):]
        lead = "/" if rest.startswith("/") else ""  # names conventionally start with '/'
        parts = rest[len(lead):].split("/")
        if depth and len(parts) > depth:
            key = prefix + lead + "/".join(parts[:depth]) + "/"
            grouped.setdefault(key, []).append(t)
        else:
            leaves.append(t)
    entries: list[PageEntry] = []
    for key, items in grouped.items():
        if len(items) == 1:  # collapsing one topic hides its name for no saving: list it directly
            leaves.append(items[0])
        else:
            entries.append(GroupEntry(key, "group", len(items)))
    entries.extend(TopicEntry(t["name"], "topic", t) for t in leaves)
    entries.sort(key=lambda entry: (entry.name, entry.tag))
    return entries


def list_header(total: int, total_items: int, hidden_names: list[str], max_chars: int) -> dict[str, Any]:
    base: dict[str, Any] = {PAGE_TOTAL: total, "total_items": total_items}
    if hidden_names:
        base["hidden_matches"] = len(hidden_names)
        if len(hidden_names) <= HIDDEN_EXAMPLES_MAX:
            base["hidden_examples"] = hidden_names
    if not fits(base, max_chars // 2) and "hidden_examples" in base:
        del base["hidden_examples"]
        base["hidden_examples_omitted"] = len(hidden_names)
    return base


def _make_page(base: dict[str, Any], page: list[PageEntry], entry_count: int, offset: int,
               fields_of: FieldsOf | None) -> dict[str, Any]:
    out = dict(base)
    page_groups: dict[str, int] = {}
    page_topics: dict[str, str] = {}
    props: dict[str, dict[str, Any]] = {}
    for entry in page:  # the tag is the discriminator: "group" carries a count, "topic" the server's row
        if entry.tag == "group":
            page_groups[entry.name] = entry.total
        else:
            row = entry.data
            page_topics[entry.name] = row["type"]
            if row["props"]:
                props[entry.name] = row["props"]
    if page_groups:
        out["groups"] = page_groups
    out["topics"] = page_topics
    if props:
        out["props"] = props
    if fields_of is not None:
        fields = fields_of(page_topics.values())
        if fields:
            out["struct_fields"] = fields
    if offset + len(page) < entry_count:
        out[PAGE_NEXT_OFFSET] = offset + len(page)
    return out


def fit_page(base: dict[str, Any], entries: list[PageEntry], offset: int, limit: int, fields_of: FieldsOf | None,
             max_chars: int) -> dict[str, Any]:
    """The page of `entries` from `offset`, shortened until its JSON fits `max_chars`.
    Keys: `total` and `total_items` from `list_header` (plus `hidden_matches` / `hidden_examples` / `hidden_examples_omitted`),
    then `topics`, `groups`, `props`, `struct_fields` and `next_offset` as filled. A page that cannot fit its first item becomes
    `truncated`, `blocked_offset` and `hint`. `fields_of(types)` supplies `struct_fields` (None leaves it out).
    """
    page = entries[offset : offset + limit]
    out = _make_page(base, page, len(entries), offset, fields_of)
    while page and not fits(out, max_chars):
        page.pop()
        out = _make_page(base, page, len(entries), offset, fields_of)
    if not page and offset < len(entries):
        out.pop(PAGE_NEXT_OFFSET, None)
        out.update(truncated=True, blocked_offset=offset, hint="Item too large; narrow the filters.")
    return out


__all__ = [
    "HIDDEN_EXAMPLES_MAX",
    "FieldsOf",
    "GroupEntry",
    "PageEntry",
    "TopicEntry",
    "collapse_topics",
    "fit_page",
    "list_header",
]

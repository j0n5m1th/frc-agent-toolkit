"""Topic discovery: the listing and its filters (`filter_topics`, pure), the group expansion, and the server's
`$` meta-topics. The functions take the connection's ntcore instance (and, for listings, the PublisherRegistry)
as arguments; `NT4Session` delegates to them."""

import json
from dataclasses import dataclass
from typing import Any, NamedTuple

import msgpack
import ntcore

from frc_mcp_wpilib.tooling.query_bases import TopicMatch
from frc_mcp_wpilib.values import UNIT_PROPS

from ..errors import NT4Error
from .publisher_registry import PublisherRegistry
from .reading import ReadMiss, get_values

SPEC_PROPS = {"persistent", "retained", "cached"}  # NT4-defined; others are app-specific
GROUP_LIMIT = 100  # topics read per group in prefix mode

Candidate = tuple[str, str, dict[str, Any]]  # (name, type string, properties)


@dataclass(frozen=True)
class TopicFilter(TopicMatch):
    """The topic-listing filters that travel together: the shared ones (`TopicMatch`) and the NT4 property filters.
    Build it with keyword arguments.
    """

    flag: str | None = None
    all_props: bool = False


class TopicListing(NamedTuple):
    """`list_topics`'s result: the matching topics sorted by name, and the names hidden as '.'-prefixed."""

    topics: list[dict[str, Any]]
    hidden_names: list[str]


class Expansion(NamedTuple):
    """`expand`'s result: the group's topic names (at most GROUP_LIMIT) and whether more were cut off."""

    names: list[str]
    truncated: bool


def _has_flag(props: dict[str, Any], flag: str | None) -> bool:
    if flag == "uncached":  # cached=false: the server keeps no value for it
        return props.get("cached") is False
    return not flag or bool(props.get(flag))


def filter_topics(candidates: list[Candidate], f: TopicFilter) -> TopicListing:
    """Returns (topics sorted by name, hidden_names): hidden = matches omitted because a path segment starts with '.'."""
    topics: list[dict[str, Any]] = []
    hidden: list[str] = []
    for name, ty, props in candidates:
        if not f.all_props:  # keep the properties NT4 defines; custom ones (e.g. source=DogLog) are noise
            props = {k: v for k, v in props.items() if k in SPEC_PROPS or k in UNIT_PROPS}
        if not f.name_matches(name) or not f.type_matches(ty) or not _has_flag(props, f.flag):
            continue
        if not f.include_hidden and "/." in name and "/." not in f.prefix:
            hidden.append(name)  # collected only after the other filters, so it means "matching but hidden"
            continue
        topics.append({"name": name, "type": ty, "props": props})
    return TopicListing(sorted(topics, key=lambda t: t["name"]), hidden)


def list_topics(inst: ntcore.NetworkTableInstance, publishers: PublisherRegistry,
                f: TopicFilter) -> TopicListing:
    """Topics the connection knows, plus our released retained/persistent ('ghost') topics, filtered by `f`.

    Returns (topics, hidden_names): see `filter_topics`.
    """
    candidates: list[Candidate] = []
    for t in inst.getTopicInfo(f.prefix):
        candidates.append((t.name, t.type_str, json.loads(t.properties) if t.properties else {}))
    live = {c[0] for c in candidates}
    for name, (ty, props) in publishers.ghosts:  # see PublisherRegistry: released retained topics
        if name.startswith(f.prefix) and name not in live and not inst.getTopic(name).exists():
            candidates.append((name, ty, props))
    return filter_topics(candidates, f)


def expand(inst: ntcore.NetworkTableInstance, base: str) -> Expansion:
    """Topics of a group: the base itself (if it is a topic) plus everything under `base/`.

    Includes '.'-prefixed children like `.type`. Returns (names, truncated_at_GROUP_LIMIT).
    """
    base = base.rstrip("/")
    names = sorted(t.name for t in inst.getTopicInfo(base + "/"))
    if inst.getTopic(base).exists():
        names.insert(0, base)
    return Expansion(names[:GROUP_LIMIT], len(names) > GROUP_LIMIT)


async def inspect_meta(inst: ntcore.NetworkTableInstance, meta_topic: str) -> list[Any]:
    """Read a server meta-topic (`$clients`, `$sub$<topic>`, ...) and decode its msgpack array.

    Our own one-shot subscription makes the server create `$sub$/x`-style topics, so allow a longer announce grace.
    Raises NT4Error: no value within 3 s, or the value is not msgpack.
    """
    res = (await get_values(inst, [meta_topic], 3.0, grace_s=1.5))[meta_topic]
    if isinstance(res, ReadMiss):
        raise NT4Error(
            "The server has no such meta-topic: the target topic/client name may be wrong (check with "
            "nt4_list_topics, or what='clients'), or the server does not publish meta-topics (WPILib servers do)."
        )
    raw = res.value.value()
    try:
        return msgpack.unpackb(bytes(raw)) if raw else []
    except (msgpack.ExtraData, msgpack.FormatError, ValueError, TypeError) as e:
        raise NT4Error(f"Could not decode {meta_topic!r} as msgpack: {e}") from e

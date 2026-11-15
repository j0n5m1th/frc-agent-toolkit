"""Size-bounded JSON replies: every tool returns one compact block, with a valid partial result when it overflows."""

import json
import math
from typing import Any

from fastmcp.tools import ToolResult

from .errors import FrcMcpError
from .settings import env_int, env_str

# One size budget for every reply of every server (there is no per-call size parameter to advertise): a small base
# for answers about nothing in particular, plus room for each topic a result covers, up to a ceiling.
BASE_CHARS = 4000  # ~1k tokens
PER_TOPIC_CHARS = 500
CEILING_CHARS = 12000  # ~3k tokens
MAX_CHARS_ENV = "MCP_WPILIB_MAX_CHARS"  # a fixed cap for every reply (overrides the adaptive budget)
MIN_FIXED_CHARS, MAX_FIXED_CHARS = 500, 50000
TRUNCATION_HINT = "Partial result; ask for fewer topics, a shorter time window, or a smaller reduce/max_samples."

# List-reply keys every server uses: `total` (all), `omitted` (left out; only when something was), `next_offset` (the
# `offset`/`since` for the rest; only when a page continues). Time-windowed pages use `next_after` (ms); several counters in one
# reply add the section name (`omitted_topics`).
PAGE_TOTAL = "total"
PAGE_OMITTED = "omitted"
PAGE_NEXT_OFFSET = "next_offset"


def to_jsonable(value: Any) -> Any:
    """Make a value JSON-safe (bytes -> hex, NaN/Inf -> string)."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def response_budget(topics: int = 0) -> int:
    """The most characters a reply may take: MCP_WPILIB_MAX_CHARS when set, else base + room per topic, up to a ceiling."""
    if env_str(MAX_CHARS_ENV):
        return env_int(MAX_CHARS_ENV, MIN_FIXED_CHARS, MIN_FIXED_CHARS, MAX_FIXED_CHARS, FrcMcpError)
    return min(CEILING_CHARS, BASE_CHARS + PER_TOPIC_CHARS * max(topics, 0))


def topic_err(detail: str) -> dict[str, str]:
    """The one shape every tool uses for a per-topic failure, so a caller checks `"err" in item` everywhere."""
    return {"err": detail}


def compact_json(data: Any) -> str:
    """Compact JSON text that the stdio writer can always encode: a lone surrogate (a file name that is not valid text) becomes a backslash-u escape."""
    return encodable(json.dumps(to_jsonable(data), separators=(",", ":"), default=str, allow_nan=False, ensure_ascii=False))


def encodable(text: str) -> str:
    """`text` that UTF-8 can always encode: a lone surrogate (a file name that is not valid text) becomes a backslash-u escape."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return text


def fits(data: Any, max_chars: int) -> bool:
    """True if `data` as compact JSON is at most `max_chars` long."""
    return len(compact_json(data)) <= max_chars


class _PartialReply:
    """The partial reply, built one entry at a time: an entry stays only if the whole reply, dumped as compact JSON, still fits."""

    def __init__(self, original_chars: int, max_chars: int) -> None:
        self.max_chars = max_chars
        self.original_chars = original_chars
        self.omitted = 0
        self.data: dict[str, Any] = {}  # the partial dict the reply reports
        self.sections: set[str] = set()  # the keys of `data` that are bulk sections (kept child by child)
        self._size = self._measure()  # the length with every entry kept so far; a new entry only ever adds to it

    def as_reply(self) -> dict[str, Any]:
        return {"truncated": True, "original_chars": self.original_chars, "omitted_items": self.omitted,
                "hint": TRUNCATION_HINT, "partial": self.data}

    def _measure(self) -> int:
        return len(compact_json(self.as_reply()))

    def fits(self) -> bool:
        """Whether the reply as built so far is within budget."""
        return self._measure() <= self.max_chars

    def start_section(self, key: str) -> None:
        """Open a bulk section as empty (its children are added one at a time)."""
        self.data[key] = {}
        self.sections.add(key)

    def add(self, key: str, value: Any, section: str | None = None) -> None:
        """Add one complete entry - top level, or inside the named bulk section - and keep it only while it fits."""
        # Cheap test first: the entry's own text can only make the reply longer, so if that alone is too much, skip the dump.
        entry_chars = len(compact_json(key)) + 1 + len(compact_json(value))
        parent = self.data if section is None else self.data[section]
        if self._size + entry_chars > self.max_chars:
            self.omitted += 1
            return
        parent[key] = value
        size = self._measure()
        if size <= self.max_chars:
            self._size = size
        else:
            del parent[key]
            self.omitted += 1

    def add_children(self, section: str, children: dict[str, Any]) -> None:
        """Add a section's children in order, each kept only while it fits.

        Entries are kept until the first one that does not fit, so the run that fits is found by doubling the count and
        then halving the gap (a few dumps) instead of dumping once per child. The children after it go one by one."""
        items = list(children.items())
        fitting, too_big = 0, 1  # `fitting` children fit; `too_big` is a count that does not (or is past the end)
        while too_big <= len(items) and self._fits_with(section, items[:too_big]):
            fitting, too_big = too_big, too_big * 2
        too_big = min(too_big, len(items) + 1)
        while too_big - fitting > 1:
            middle = (fitting + too_big) // 2
            if self._fits_with(section, items[:middle]):
                fitting = middle
            else:
                too_big = middle
        self.data[section] = dict(items[:fitting])
        self._size = self._measure()
        for key, value in items[fitting:]:
            self.add(key, value, section)

    def _fits_with(self, section: str, items: list[tuple[str, Any]]) -> bool:
        self.data[section] = dict(items)
        return self.fits()

    def drop_last(self, bulk_keys: tuple[str, ...]) -> None:
        """Remove the last top-level entry (a whole section goes with its children); each item it held counts as omitted."""
        key = next(reversed(self.data))
        value = self.data.pop(key)
        self.sections.discard(key)
        self.omitted += len(value) if key in bulk_keys and isinstance(value, dict) else 1


def _fill_partial(reply_: _PartialReply, data: Any, bulk_keys: tuple[str, ...]) -> None:
    if not isinstance(data, dict):
        reply_.omitted = 1
        return
    for key, value in data.items():
        if key in bulk_keys and isinstance(value, dict):
            reply_.start_section(key)
            reply_.add_children(key, value)
        else:
            reply_.add(key, value)


def _trim_to_fit(reply_: _PartialReply, bulk_keys: tuple[str, ...]) -> None:
    """The number of omitted items can grow a digit while fitting later entries: drop from the end until it fits."""
    while not reply_.fits() and reply_.data:
        reply_.drop_last(bulk_keys)


def _topic_count(data: Any, bulk_keys: tuple[str, ...]) -> int:
    """How many distinct topics a result covers: the children across its bulk sections (a topic may be in several), else 0."""
    if not isinstance(data, dict):
        return 0
    return len({child for key in bulk_keys if isinstance(data.get(key), dict) for child in data[key]})


def reply(data: Any, bulk_keys: tuple[str, ...] = (), *, topics: int | None = None, max_chars: int | None = None) -> ToolResult:
    """Return one bounded JSON block, keeping only complete items in an oversized result.
    Budget: `BASE_CHARS` + `PER_TOPIC_CHARS` per covered topic (`topics`, else the distinct children in the `bulk_keys` sections),
    capped at `CEILING_CHARS`; `MCP_WPILIB_MAX_CHARS` (500-50000) replaces that with a fixed cap. Over budget the reply is refilled
    key by key, and the `bulk_keys` top-level sections keep whole children. `max_chars` forces a budget (internal callers, tests).
    `reply_halving` is the other policy (the profiling server's).
    """
    if max_chars is None:
        max_chars = response_budget(_topic_count(data, bulk_keys) if topics is None else topics)
    text = compact_json(data)
    if len(text) <= max_chars:
        return ToolResult(content=text)
    result = _PartialReply(len(text), max_chars)
    if not result.fits():
        return ToolResult(content=compact_json({"truncated": True, "hint": TRUNCATION_HINT}))
    _fill_partial(result, data, bulk_keys)
    _trim_to_fit(result, bulk_keys)
    partial = compact_json(result.as_reply())
    if len(partial) > max_chars:  # even with every entry dropped the reply is too big
        return ToolResult(content=compact_json({"truncated": True, "hint": TRUNCATION_HINT}))
    return ToolResult(content=partial)


HALVING_HINT = "Partial result; ask for fewer rows (top=) or a narrower question."


def reply_halving(data: dict[str, Any], max_chars: int, hint: str = HALVING_HINT) -> ToolResult:
    """One compact JSON block within `max_chars`, by the other policy (the profiling server's): over the cap, the longest list is halved until it fits.

    The keys `truncated`, `omitted_items` and `hint` are added beside the data (`reply` nests what is left under `partial` instead), and a result with no list left to halve is
    just `{"truncated": true, "hint": ...}`. The cap is fixed by the caller (the profiling server reads `JFR_MAX_CHARS`), not adaptive like `response_budget`.
    """
    dropped = 0
    while True:
        shown = {**data, "truncated": True, "omitted_items": dropped, "hint": hint} if dropped else data
        if len(text := compact_json(shown)) <= max_chars:  # measured with the added keys, so the final reply is within the cap
            return ToolResult(content=text)
        lists = [(k, v) for k, v in data.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            return ToolResult(content=compact_json({"truncated": True, "hint": hint}))
        key, items = max(lists, key=lambda kv: len(compact_json(kv[1])))
        dropped += len(items) - len(items) // 2
        data = {**data, key: items[: len(items) // 2]}

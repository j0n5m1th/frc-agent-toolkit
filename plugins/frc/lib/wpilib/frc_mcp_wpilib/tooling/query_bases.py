"""The two records the NT4 and log servers each extend: what a topic listing filters on, and the reduce knobs a question carries.
Both servers answer the same questions about different data, so they share these fields and matching rules; each server's own
`TopicFilter` and `QuerySpec` adds what only that side has. Build subclasses with keyword arguments.
"""

from dataclasses import dataclass

from ..reduce.types import Reduce
from ..values import DEFAULT_PRECISION


def contains_any(needle: str, text: str) -> bool:
    """True if `text` holds `needle` (any case); `a|b` is true when it holds either part. A needle with a space beside a `|` is searched as written."""
    low = text.lower()
    if "|" in needle and any(part != part.strip() for part in needle.split("|")):
        return needle.lower() in low
    parts = [p for p in needle.lower().split("|") if p]
    return not parts or any(part in low for part in parts)


@dataclass(frozen=True)
class TopicMatch:
    """The topic-listing filters both servers have: where names start, what they contain, which type, and whether to show hidden topics.

    What counts as hidden differs (a log also hides its schema topics), so each server decides that itself.
    """

    prefix: str = ""
    name_contains: str | None = None
    type_str: str | None = None
    include_hidden: bool = False

    def name_matches(self, name: str) -> bool:
        """True if `name` contains `name_contains` (any case), or none was given."""
        return not self.name_contains or contains_any(self.name_contains, name)

    def type_matches(self, type_str: str) -> bool:
        """True if `type_str` is the wanted type, or none was given."""
        return not self.type_str or type_str == self.type_str


@dataclass(frozen=True)
class ReduceKnobs:
    """The reduce settings of one question about topic values: how to reduce, which struct fields, and how much to return."""

    reduce: Reduce = "summary"
    fields: list[str] | None = None
    threshold: float | None = None
    max_samples: int = 20
    max_topics: int = 40
    precision: int = DEFAULT_PRECISION

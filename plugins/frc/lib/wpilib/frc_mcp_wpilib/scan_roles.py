"""Which topic plays which role in a scan, and why. A word in a name is not proof of what a topic measures.

A topic is used for a role only when the caller named it, or when it follows a logging convention (a leaf name the libraries publish).
A topic that only has a suggestive name is listed as a candidate and is not used, because a wrong guess looks just like a right
answer: the first topic with "volt" in its name can be a 5 V rail, not the battery.

This is shared by the log server and the live server, so a recorded match and a live robot are scanned by the same rules.
"""

from dataclasses import dataclass, field
from typing import NamedTuple

from .errors import WPILibMCPError

BATTERY_LEAVES = ("batteryvoltage",)
BATTERY_PARENTS = ("powerdistribution", "pdh", "pdp", "battery")  # a leaf `Voltage` under one of these is the battery
BROWNOUT_LEAVES = ("brownedout", "isbrownedout", "brownout")
MAX_CANDIDATES = 3
NUMBER_TYPES = ("double", "float", "int")  # NetworkTables spelling: a WPILOG int64 is `int`


class ScanError(WPILibMCPError):
    """A named topic cannot be used: it is missing or is not a number."""


class ScanTopic(NamedTuple):
    """What a scan needs to know about a topic. `updates` and `last_us` are 0 and None when the source does not know them (a live robot)."""

    name: str
    type_str: str
    updates: int = 0
    unit: str | None = None
    last_us: int | None = None

    @property
    def is_numeric(self) -> bool:
        return self.type_str in NUMBER_TYPES


@dataclass
class Choice:
    """The topics chosen for a role, how they were chosen, and the other topics worth confirming (never used)."""

    topics: list[ScanTopic] = field(default_factory=list)
    by: str = "none"  # explicit, convention or none
    others: list[str] = field(default_factory=list)


def leaf_and_parents(name: str) -> tuple[str, list[str]]:
    """The last path segment and the folders above it, lower case, with an index such as `[0]` stripped from each folder."""
    *parents, leaf = name.lower().replace(":", "/").split("/")
    return leaf, [p.split("[")[0] for p in parents]


def by_updates(topics: list[ScanTopic]) -> list[ScanTopic]:
    return sorted(topics, key=lambda t: (-t.updates, t.name))


def _candidates(visible: list[ScanTopic], used: set[str], *words: str) -> list[str]:
    """Topics whose name holds one of `words` but that were not used, most updates first."""
    found = [t for t in visible if t.name not in used and any(w in t.name.lower() for w in words)]
    return [t.name for t in by_updates(found)[:MAX_CANDIDATES]]


def battery_choice(visible: list[ScanTopic], explicit: str | None, known: dict[str, ScanTopic], not_found: str = "") -> Choice:
    """The battery voltage: the topic passed in `explicit`, else a topic that follows a convention (`BatteryVoltage`, or `Voltage`
    under a power distribution or battery path), the one with the most updates when several do. Otherwise none, and the
    name-only matches are listed. `known` holds every topic (hidden ones too); `not_found` is a hint added to the missing-topic error."""
    numbers = [t for t in visible if t.is_numeric]
    if explicit:
        if explicit not in known:
            raise ScanError(f"battery {explicit!r} is not among the topics{f' ({not_found})' if not_found else ''}.")
        if not known[explicit].is_numeric:
            raise ScanError(f"battery {explicit!r} is type {known[explicit].type_str}; it must be a number (double, float or int).")
        return Choice([known[explicit]], "explicit", _candidates(numbers, {explicit}, "battery"))
    matched = []
    for t in numbers:
        leaf, parents = leaf_and_parents(t.name)
        if leaf in BATTERY_LEAVES or (leaf == "voltage" and any(p in BATTERY_PARENTS for p in parents)):
            matched.append(t)
    matched.sort(key=lambda t: (leaf_and_parents(t.name)[0] not in BATTERY_LEAVES, -t.updates, t.name))  # a leaf named for the battery first
    if matched:
        return Choice(matched[:1], "convention", [t.name for t in matched[1:MAX_CANDIDATES + 1]])
    return Choice(others=_candidates(numbers, set(), "battery", "volt"))


def brownout_choice(visible: list[ScanTopic]) -> Choice:
    """The brownout flags: boolean topics whose leaf name the libraries publish. A boolean named like a brownout in some other way is listed."""
    booleans = [t for t in visible if t.type_str == "boolean"]
    matched = by_updates([t for t in booleans if leaf_and_parents(t.name)[0] in BROWNOUT_LEAVES])
    if matched:
        return Choice(matched, "convention")
    return Choice(others=_candidates(booleans, set(), "brown"))

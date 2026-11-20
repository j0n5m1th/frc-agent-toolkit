"""Which topic plays which role in a scan, and why. A word in a name is not proof of what a topic measures.

A topic is used for a role only when the caller named it, or when it follows a logging convention (a leaf name the libraries publish).
A topic that only has a suggestive name is listed as a candidate and is not used, because a wrong guess looks just like a right
answer: the first topic with "volt" in its name can be a 5 V rail, not the battery.
"""

from dataclasses import dataclass, field

from .errors import WPILogError
from .logfile import Topic
from .windows import FIND_NAMES_HINT, NOT_IN_LOG

BATTERY_LEAVES = ("batteryvoltage",)
BATTERY_PARENTS = ("powerdistribution", "pdh", "pdp", "battery")  # a leaf `Voltage` under one of these is the battery
BROWNOUT_LEAVES = ("brownedout", "isbrownedout", "brownout")
MAX_CANDIDATES = 3


@dataclass
class Choice:
    """The topics chosen for a role, how they were chosen, and the other topics worth confirming (never used)."""

    topics: list[Topic] = field(default_factory=list)
    by: str = "none"  # explicit, convention or none
    others: list[str] = field(default_factory=list)


def _leaf_and_parents(name: str) -> tuple[str, list[str]]:
    *parents, leaf = name.lower().replace(":", "/").split("/")
    return leaf, [p.split("[")[0] for p in parents]


def _by_updates(topics: list[Topic]) -> list[Topic]:
    return sorted(topics, key=lambda t: (-t.count, t.name))


def _candidates(visible: list[Topic], used: set[str], *words: str) -> list[str]:
    """Topics whose name holds one of `words` but that were not used, most updates first."""
    found = [t for t in visible if t.name not in used and any(w in t.name.lower() for w in words)]
    return [t.name for t in _by_updates(found)[:MAX_CANDIDATES]]


def battery_choice(visible: list[Topic], explicit: str | None, topics: dict[str, Topic]) -> Choice:
    """The battery voltage: the topic passed in `explicit`, else a topic that follows a convention (`BatteryVoltage`, or `Voltage`
    under a power distribution or battery path), the one with the most updates when several do. Otherwise none, and the
    name-only matches are listed."""
    numbers = [t for t in visible if t.is_numeric]
    if explicit:
        if explicit not in topics:
            raise WPILogError(f"battery {explicit!r} is {NOT_IN_LOG} ({FIND_NAMES_HINT}).")
        if not topics[explicit].is_numeric:
            raise WPILogError(f"battery {explicit!r} is type {topics[explicit].type_str}; it must be a number (double, float or int).")
        if (why := topics[explicit].unusable_for("`wpilog_scan`")) is not None:
            raise WPILogError(why)
        return Choice([topics[explicit]], "explicit", _candidates(numbers, {explicit}, "battery"))
    matched = []
    for t in numbers:
        leaf, parents = _leaf_and_parents(t.name)
        if leaf in BATTERY_LEAVES or (leaf == "voltage" and any(p in BATTERY_PARENTS for p in parents)):
            matched.append(t)
    matched.sort(key=lambda t: (_leaf_and_parents(t.name)[0] not in BATTERY_LEAVES, -t.count, t.name))  # a leaf named for the battery first
    if matched:
        return Choice(matched[:1], "convention", [t.name for t in matched[1:MAX_CANDIDATES + 1]])
    return Choice(others=_candidates(numbers, set(), "battery", "volt"))


def brownout_choice(visible: list[Topic]) -> Choice:
    """The brownout flags: boolean topics whose leaf name the libraries publish. A boolean named like a brownout in some other way is listed."""
    booleans = [t for t in visible if t.type_str == "boolean"]
    matched = _by_updates([t for t in booleans if _leaf_and_parents(t.name)[0] in BROWNOUT_LEAVES])
    if matched:
        return Choice(matched, "convention")
    return Choice(others=_candidates(booleans, set(), "brown"))

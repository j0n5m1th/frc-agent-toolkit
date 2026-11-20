"""Power checks shared by the scan and the power tools: the brownout setting, and which device drew the current when the battery sagged.

A Systemcore browns out below a voltage that is configurable (6 V nominal; the vendor specification also lists 6.3 V with a 7.5 V restoration voltage,
both configurable, and says brownout protection is off in some alpha images). So the number is read from the robot when it publishes it, and only
otherwise assumed, and a reply says which.

Current is read only from signals with a conventional name (a power distribution `ChannelCurrent` array or `Chan<N>` signals, and the motor-controller
current signals listed below), or from the topics the caller names. A device that has only a suggestive name is listed, not used. A channel number
becomes a device name only through the map the robot code publishes (`robot_info`).
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from .reduce.stats import US_PER_S
from .robot_info import info_topics, parse_map
from .scan_roles import (
    MAX_CANDIDATES,
    Choice,
    ScanError,
    ScanTopic,
    battery_choice,
    brownout_choice,
    by_updates,
    leaf_and_parents,
)
from .scan_rows import Row, Window, finite_numbers, value_at, within
from .tracking import NothingToCompare, seconds_since

BROWNOUT_DEFAULT_V = 6.0  # the Systemcore's nominal setting (vendor specification, not seen on a robot here)
BROWNOUT_LEAVES = ("brownoutvoltage",)
DEFAULT_BASIS = "default: the Systemcore's nominal setting, which is configurable"
ARRAY_LEAVES = ("channelcurrent",)  # a double[] with one number per power distribution channel
CURRENT_LEAVES = ("statorcurrent", "supplycurrent", "torquecurrent", "outputcurrent", "currentamps")  # one device's current, in amps
CHANNEL_LEAF = re.compile(r"chan(\d+)$")  # a WPILib power distribution sendable's `Chan<N>`
MAX_LISTED = 8  # devices in the peak list
MAX_AT_LOW = 5  # devices listed for the moment the battery was lowest
NUMBER_ARRAYS = ("double[]", "float[]", "int[]")


def threshold_topic(visible: Sequence[ScanTopic]) -> ScanTopic | None:
    """The topic that holds the robot's brownout setting: a numeric leaf named `BrownoutVoltage`."""
    found = [t for t in visible if t.is_numeric and leaf_and_parents(t.name)[0] in BROWNOUT_LEAVES]
    return by_updates(found)[0] if found else None


def brownout_threshold(topic: ScanTopic | None, rows: Sequence[Row], given: float | None = None) -> tuple[float, str]:
    """`(volts, basis)`: the value the caller gave, else the robot's own setting (its last finite reading), else the Systemcore's nominal one."""
    if given is not None:
        return given, "given"
    if topic is not None and (values := finite_numbers(rows)):
        return float(values[-1][1]), f"logged ({topic.name})"
    return BROWNOUT_DEFAULT_V, DEFAULT_BASIS


class CurrentSources(NamedTuple):
    topics: list[ScanTopic]  # to read
    by: str  # explicit or convention
    candidates: list[str]  # named like a current but not used


def current_sources(visible: Sequence[ScanTopic], given: Sequence[str] = (), known: Mapping[str, ScanTopic] | None = None) -> CurrentSources:
    """The current signals: the ones the caller named (any topic in `known`, hidden ones too), else the conventional ones. Others with `current`
    or `amps` in the name are only listed."""
    if given:
        by_name = known or {t.name: t for t in visible}
        return CurrentSources([by_name[n] for n in given if n in by_name], "explicit", [])
    chosen = []
    for t in visible:
        leaf, parents = leaf_and_parents(t.name)
        if (t.type_str in NUMBER_ARRAYS and leaf in ARRAY_LEAVES) or (t.is_numeric and (leaf in CURRENT_LEAVES or (CHANNEL_LEAF.match(leaf) and any(p.startswith("powerdistribution") for p in parents)))):
            chosen.append(t)
    named = [t for t in visible if t not in chosen and (t.is_numeric or t.type_str in NUMBER_ARRAYS) and any(w in t.name.lower() for w in ("current", "amps"))]
    return CurrentSources(chosen, "convention", [t.name for t in by_updates(named)[:MAX_CANDIDATES]] if not chosen else [])


class Series(NamedTuple):
    source: str  # the topic
    channel: int | None  # which element of an array, or None for a single signal
    rows: list[Row]


def expand(topic: ScanTopic, rows: Sequence[Row]) -> list[Series]:
    """One series per signal: a scalar topic is one; an array topic is one per channel. A `Chan<N>` signal is channel N."""
    leaf, _ = leaf_and_parents(topic.name)
    if topic.type_str in NUMBER_ARRAYS:
        by_channel: defaultdict[int, list[Row]] = defaultdict(list)
        for t, values in rows:
            for i, v in enumerate(values):
                by_channel[i].append((t, float(v)))
        return [Series(topic.name, i, r) for i, r in sorted(by_channel.items())]
    return [Series(topic.name, int(m.group(1)) if (m := CHANNEL_LEAF.match(leaf)) else None, list(rows))]


def _intervals(flag_rows: Sequence[Row], windows: Sequence[Window], end_us: int) -> list[tuple[int, int]]:
    """When a boolean was true: each update holds until the next (the last until `end_us`), but not past the end of the window it began in."""
    rows = [r for r in flag_rows if isinstance(r[1], bool)]
    out: list[tuple[int, int]] = []
    for i, (t, v) in enumerate(rows):
        nxt = rows[i + 1][0] if i + 1 < len(rows) else end_us
        if windows:
            hi = next((hi for lo, hi in windows if lo <= t <= hi), None)
            if hi is None:
                continue
            nxt = min(nxt, hi)
        if v and nxt > t:
            if out and out[-1][1] == t:  # the flag stayed true: one stretch, not one per update
                out[-1] = (out[-1][0], nxt)
            else:
                out.append((t, nxt))
    return out


def _together(windows: Sequence[Window], t0: int, t1: int) -> bool:
    """True when both times are in one window, so the stretch between two readings does not cross a disabled gap."""
    return not windows or any(lo <= t0 and t1 <= hi for lo, hi in windows)


def analyze_power(voltage: Sequence[Row], flags: Sequence[Sequence[Row]], series: Iterable[Series], threshold: tuple[float, str], windows: Sequence[Window],
                  origin_us: int, labels: Mapping[int, str] | None = None) -> dict[str, Any]:
    """The battery low point, the brownouts, the devices with the highest current, and what each was drawing when the battery was lowest.

    `voltage` and the current rows are `(microseconds, value)`. Raises NothingToCompare when there is nothing to check."""
    brownout_v, basis = threshold
    volts = [r for r in within(finite_numbers(voltage), windows) if float(r[1]) > 0]  # 0 V is no battery (bench, simulation)
    lowest = min(volts, key=lambda r: r[1]) if volts else None  # (time, volts)
    low_t = lowest[0] if lowest else None
    peaks = []  # (amps, signal, the peak row); a signal's readings are dropped once it is summarised, so a wide array never sits in memory all at once
    then = []  # (amps, signal, signed amps) at the battery's low point
    for s in series:
        if not (inside := within(finite_numbers(s.rows), windows)):
            continue
        tag = s._replace(rows=[])
        peak = max(inside, key=lambda r: abs(float(r[1])))
        peaks.append((abs(float(peak[1])), tag, peak))
        if low_t is not None and (v := value_at(inside, low_t)) is not None:
            then.append((abs(float(v)), tag, float(v)))
    end_us = windows[-1][1] if windows else (volts[-1][0] if volts else 0)
    brownouts = [iv for rows in flags for iv in _intervals(rows, windows, end_us)]
    if not volts and not peaks and not brownouts:
        raise NothingToCompare("no battery voltage, current or brownout flag has a reading in the time checked")
    out: dict[str, Any] = {}
    if lowest:
        low_t, low = lowest
        under = sum(b[0] - a[0] for a, b in zip(volts, volts[1:], strict=False) if float(a[1]) < brownout_v and _together(windows, a[0], b[0])) / US_PER_S
        out["voltage"] = {"min_v": round(float(low), 2), "min_at_s": seconds_since(low_t, origin_us), "below_brownout_s": round(under, 1),
                          "brownout_v": brownout_v, "brownout_basis": basis}
    if brownouts:
        out["brownouts"] = {"count": len(brownouts), "total_s": round(sum(b - a for a, b in brownouts) / US_PER_S, 1),
                            "first": [[seconds_since(a, origin_us), seconds_since(b, origin_us)] for a, b in sorted(brownouts)[:3]]}

    def entry(s: Series, **more: Any) -> dict[str, Any]:
        row: dict[str, Any] = {"source": s.source}
        if s.channel is not None:
            row["channel"] = s.channel
            if labels and s.channel in labels:
                row["name"] = labels[s.channel]
        return row | more

    peaks.sort(key=lambda p: -p[0])
    out["top_current"] = [entry(s, peak_a=round(a, 1), at_s=seconds_since(at[0], origin_us),
                                battery_v_then=round(float(v), 2) if (v := value_at(volts, at[0])) is not None else None) for a, s, at in peaks[:MAX_LISTED]]
    if low_t is not None:
        then.sort(key=lambda p: -p[0])
        out["at_lowest_voltage"] = [entry(s, amps=round(a, 1)) for _, s, a in then[:MAX_AT_LOW]]
    out["labelled"] = bool(labels)
    return out


class PowerPlan(NamedTuple):
    battery: Choice
    browned: Choice
    threshold: ScanTopic | None
    sources: CurrentSources
    info: dict[str, str]  # robot-info topics, `pdh` and `can`

    @property
    def names(self) -> list[str]:
        """Every topic whose rows the check reads, once each."""
        wanted = [*(t.name for t in self.battery.topics), *(t.name for t in self.browned.topics), *([self.threshold.name] if self.threshold else []),
                  *(t.name for t in self.sources.topics), *([self.info["pdh"]] if "pdh" in self.info else [])]
        return list(dict.fromkeys(wanted))

    def facts(self) -> dict[str, Any]:
        """What was found, for a reply that could not check anything: the battery candidates and the current signals that were only listed."""
        out: dict[str, Any] = {}
        if self.battery.others:
            out["battery_candidates"] = self.battery.others
        if self.sources.candidates:
            out["current_candidates"] = self.sources.candidates
        return out


def plan_power(visible: Sequence[ScanTopic], known: dict[str, ScanTopic], battery: str | None = None, currents: Sequence[str] = (), not_found: str = "") -> PowerPlan:
    """Choose the topics to read. Raises ScanError for a named battery or current that is missing or not a number."""
    for name in currents:
        if name not in known:
            raise ScanError(f"current {name!r} is not among the topics{f' ({not_found})' if not_found else ''}.")
        if not (known[name].is_numeric or known[name].type_str in NUMBER_ARRAYS):
            raise ScanError(f"current {name!r} is type {known[name].type_str}; it must be a number or an array of numbers.")
    return PowerPlan(battery_choice(list(visible), battery, known, not_found), brownout_choice(list(visible)), threshold_topic(visible),
                     current_sources(visible, currents, known), info_topics(visible))


def run_power(plan: PowerPlan, rows: Mapping[str, Sequence[Row]], windows: Sequence[Window], origin_us: int, brownout_v: float | None = None) -> dict[str, Any]:
    """The power reply. Raises NothingToCompare when no battery, current or brownout flag has a reading to check."""
    threshold = brownout_threshold(plan.threshold, rows[plan.threshold.name] if plan.threshold else (), brownout_v)
    battery = plan.battery.topics[0] if plan.battery.topics else None
    series = (s for t in plan.sources.topics for s in expand(t, rows[t.name]))
    labels = parse_map(rows[plan.info["pdh"]]) if "pdh" in plan.info else {}
    out = analyze_power(rows[battery.name] if battery else (), [rows[t.name] for t in plan.browned.topics], series, threshold, windows, origin_us, labels)
    if battery:
        out["battery"] = {"topic": battery.name, "chosen_by": plan.battery.by, **({"others": plan.battery.others} if plan.battery.others else {})}
    out["currents"] = {"chosen_by": plan.sources.by, "topics": [t.name for t in plan.sources.topics]}
    if any("channel" in r for r in out["top_current"]) and not labels:
        out["hint"] = "channels are numbers: the robot can publish a PdhChannels map so they show device names (docs/ROBOT_INFO.md)"
    return out

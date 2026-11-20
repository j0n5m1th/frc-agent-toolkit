"""Two more checks for `scan`: how long the robot's loop took, and the CAN bus utilization and error counters.

Both read only topics with a conventional name (AdvantageKit's `LoggedRobot/FullCycleMS` and `UserCodeMS`; WPILib's CAN status fields as
AdvantageKit logs them under a `CANBus` path), never a topic that only has a suggestive name: those are listed as candidates.
"""

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .reduce.stats import percentile
from .scan_roles import MAX_CANDIDATES, ScanTopic, leaf_and_parents
from .scan_rows import Row, Window, finite_numbers

LOOP_PERIOD_MS = 20.0  # the standard 50 Hz robot loop
BOOT_CYCLE_FACTOR = 10  # a first loop this many times the median is the slow boot cycle, left out
ERROR_PASSIVE = 128  # a CAN controller is error-passive from a transmit or receive error count of 128, and goes bus-off past 255
HIGH_UTILIZATION_PCT = 80.0  # a CAN bus busier than this risks timeouts
OVER_SHARE_LOW = 0.01  # this share of loops over the period is worth a low flag
LOOP_FULL, LOOP_USER = "fullcyclems", "usercodems"
LOOP_WORDS = ("looptime", "cycletime", "fullcycle")  # a name like this that is not the convention is only listed
COUNTERS = {
    "tec": "tec", "transmiterrorcount": "tec", "rec": "rec", "receiveerrorcount": "rec", "busoffcount": "bus_off", "offcount": "bus_off",
    "txfullcount": "tx_full", "utilization": "util", "busutilization": "util", "percentbusutilization": "util",
}


def loop_topics(topics: Sequence[ScanTopic]) -> tuple[ScanTopic | None, ScanTopic | None, list[str]]:
    """The full-cycle and user-code loop time topics (under `LoggedRobot`), and name-only candidates when there is no full-cycle topic."""
    found: dict[str, ScanTopic] = {}
    for t in topics:
        leaf, parents = leaf_and_parents(t.name)
        if t.is_numeric and leaf in (LOOP_FULL, LOOP_USER) and "loggedrobot" in parents:
            found.setdefault(leaf, t)
    full = found.get(LOOP_FULL)
    named = sorted((t for t in topics if t.is_numeric and any(w in t.name.lower() for w in LOOP_WORDS) and t not in found.values()), key=lambda t: (-t.updates, t.name))
    return full, found.get(LOOP_USER), ([] if full else [t.name for t in named[:MAX_CANDIDATES]])


def counter_topics(topics: Sequence[ScanTopic]) -> dict[str, dict[str, ScanTopic]]:
    """CAN bus path -> {tec, rec, bus_off, tx_full, util} -> topic, found by exact field name under a `CAN` path (never a substring such as "can")."""
    buses: dict[str, dict[str, ScanTopic]] = defaultdict(dict)
    for t in topics:
        leaf, parents = leaf_and_parents(t.name)
        if t.is_numeric and leaf in COUNTERS and any("can" in p for p in parents):
            buses[t.name.rsplit("/", 1)[0]].setdefault(COUNTERS[leaf], t)
    return dict(buses)


def _runs(rows: Sequence[Row], windows: Sequence[Window]) -> list[list[float]]:
    """The finite values inside each enabled period, one list per period (one list of every value when the log has no Driver Station data)."""
    numbers = finite_numbers(rows)
    if not windows:
        return [[float(v) for _, v in numbers]]
    starts = [start for start, _ in windows]
    runs: list[list[float]] = [[] for _ in windows]
    for t, v in numbers:
        if (i := bisect_right(starts, t) - 1) >= 0 and t <= windows[i][1]:
            runs[i].append(float(v))
    return runs


def _rise(run: Sequence[float]) -> float:
    """How much a counter that only grows rose over a run; a drop is a counter reset, not a negative rise."""
    return sum(max(0.0, b - a) for a, b in zip(run, run[1:], strict=False))


def _loop_values(rows: Sequence[Row], windows: Sequence[Window]) -> list[float]:
    """The loop times while enabled, in time order, without the slow boot cycle."""
    values = [v for run in _runs(rows, windows) for v in run]
    if len(values) > 1 and values[0] > BOOT_CYCLE_FACTOR * percentile(sorted(values), 50):
        values = values[1:]
    return values


def loop_check(rows: Mapping[str, Sequence[Row]], full: ScanTopic | None, user: ScanTopic | None, candidates: list[str], windows: Sequence[Window]) -> tuple[dict[str, Any], list[str]]:
    """The loop-time block and its flags. Times in the enabled periods only (all, when there is no Driver Station data)."""
    if full is None:
        block: dict[str, Any] = {"checked": False, "why": "no LoggedRobot/FullCycleMS topic"}
        if candidates:
            block |= {"candidates": candidates, "hint": "named like a loop time but not used; wpilog_query one"}
        return block, []
    values = _loop_values(rows[full.name], windows)
    if not values:
        return {"checked": False, "topic": full.name, "why": "no loop times while enabled"}, []
    ordered = sorted(values)
    stats = {
        "n": len(values), "median_ms": round(percentile(ordered, 50), 1), "p95_ms": round(percentile(ordered, 95), 1), "max_ms": round(ordered[-1], 1),
        "over_period_pct": round(100 * sum(v > LOOP_PERIOD_MS for v in values) / len(values), 1),
    }
    block = {"topic": full.name, "scope": "enabled" if windows else "all", **stats}
    if user is not None and (user_values := _loop_values(rows[user.name], windows)):
        block["user_code_p95_ms"] = round(percentile(sorted(user_values), 95), 1)  # robot code alone, without the logging a full cycle includes
    flags = []
    if stats["p95_ms"] > LOOP_PERIOD_MS:
        flags.append(f"med: loop time p95 was {stats['p95_ms']} ms, over the {LOOP_PERIOD_MS:g} ms period, with {stats['over_period_pct']}% of loops over (worst {stats['max_ms']} ms)")
    elif stats["over_period_pct"] >= 100 * OVER_SHARE_LOW:
        flags.append(f"low: {stats['over_period_pct']}% of loops took over {LOOP_PERIOD_MS:g} ms (worst {stats['max_ms']} ms)")
    return block, flags


def _bus_row(bus: str, fields: dict[str, ScanTopic], rows: Mapping[str, Sequence[Row]], windows: Sequence[Window]) -> dict[str, Any]:
    """One bus's numbers while enabled: the highest TEC, REC and utilization (levels), and the rise of the counts that only grow."""
    row: dict[str, Any] = {"bus": bus.rsplit("/", 1)[-1] or bus}
    for key, topic in sorted(fields.items()):
        runs = [run for run in _runs(rows[topic.name], windows) if run]
        if not runs:
            continue
        if key == "util":
            peak = max(max(run) for run in runs)
            if peak <= 1.0 and topic.unit != "%" and "systemstats" in topic.name.lower():
                peak *= 100  # SystemStats may log a 0-1 fraction (assumed, not seen on a real match log)
            row["util_max_pct"] = round(peak, 1)
        elif key in ("tec", "rec"):
            row[f"{key}_max_enabled"] = int(max(max(run) for run in runs))
        else:  # counts that only grow: the rise inside each enabled period, so errors while disabled never count
            row[f"{key}_rise_enabled"] = int(sum(_rise(run) for run in runs))
    return row


def _bus_flags(row: dict[str, Any]) -> list[str]:
    """Flags for one bus row. Errors while disabled are normal (devices boot and time out), so only the enabled numbers count."""
    bus, flags = row["bus"], []
    if row.get("bus_off_rise_enabled", 0) > 0:
        flags.append(f"high: CAN bus {bus} went bus-off {row['bus_off_rise_enabled']} time(s) while enabled")
    for key in ("tec", "rec"):
        if row.get(f"{key}_max_enabled", 0) >= ERROR_PASSIVE:
            flags.append(f"med: CAN bus {bus} {key.upper()} reached {row[f'{key}_max_enabled']} while enabled (error-passive from {ERROR_PASSIVE})")
    if row.get("util_max_pct", 0) > HIGH_UTILIZATION_PCT:
        flags.append(f"med: CAN bus {bus} utilization peaked at {row['util_max_pct']}% (over {HIGH_UTILIZATION_PCT:g}% risks timeouts)")
    if row.get("tx_full_rise_enabled", 0) > 0:
        flags.append(f"low: CAN bus {bus} transmit buffer filled {row['tx_full_rise_enabled']} time(s) while enabled")
    return flags


def can_check(rows: Mapping[str, Sequence[Row]], buses: dict[str, dict[str, ScanTopic]], windows: Sequence[Window]) -> tuple[list[dict[str, Any]], list[str]]:
    """One row per CAN bus, and the flags they raise."""
    out = [row for bus, fields in sorted(buses.items()) if len(row := _bus_row(bus, fields, rows, windows)) > 1]
    return out, [flag for row in out for flag in _bus_flags(row)]

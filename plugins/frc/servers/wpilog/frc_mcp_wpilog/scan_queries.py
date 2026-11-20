"""`scan`: the usual trouble in one log, checked the same way every time, as one small reply.

Checks: errors and warnings in the log text, loop overruns, battery sag and brownouts, Driver Station link numbers, signals that
stopped before the log ended, and how often the robot was enabled. Each check says what it found or why it could not run, so a
quiet reply is never mistaken for a clean log.
"""

import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from frc_mcp_wpilib.reduce.stats import US_PER_S

from .logfile import Topic, serialized
from .loglike import LogLike
from .loop_can_checks import can_check, counter_topics, loop_check, loop_topics
from .rows import LogRow, Rows, finite_numbers
from .signal_roles import Choice, battery_choice, brownout_choice
from .swerve_checks import analysis_windows, log_pair, scan_block
from .text_queries import text_topics
from .timeline import origin_us, phases

LOW_VOLTS, BROWNOUT_VOLTS = 10.0, 7.0  # the roboRIO can brown out near 7 V
MIN_SILENT_UPDATES = 100  # a topic with fewer updates is event-driven; its silence says nothing
MIN_SILENT_S = 10.0
MANY_STOPPED = 5  # this many signals stopping early is read as one cause, not several faults
MANY_ENABLES = 6  # more enabled periods than this in one log is worth a look
MAX_EXAMPLES = 3
LINK = (("packet_loss_pct", ("packetloss",), 5.0), ("trip_ms", ("tripti",), 100.0), ("cpu_pct", ("cpu", "util"), 90.0))
FRACTION_LABELS = ("cpu_pct",)  # a SystemStats topic may be a 0-1 fraction (assumed, not seen on a real match log); the Driver Station log is percent
_NUMBER = re.compile(r"\d+(\.\d+)?")
_ERROR_WORD = re.compile(r"\b(?:error|fault|exception)s?\b")  # whole words: "default" is not a fault

Visible = list[tuple[Topic, str]]  # (topic, lower-case name) of the topics a check may look at, built once per scan
TopicRows = dict[str, Rows]  # topic name -> its rows, read in one pass


def _is_bool(topic: Topic) -> bool:
    return topic.type_str == "boolean"


def _pick(visible: Visible, *words: str) -> list[Topic]:
    """Number topics whose lower-case name holds every word, most updates first."""
    found = [t for t, low in visible if t.is_numeric and all(w in low for w in words)]
    return sorted(found, key=lambda t: (-t.count, t.name))


def _numbers(rows: TopicRows, topic: Topic) -> list[LogRow]:
    return finite_numbers(rows[topic.name])


def _held_s(rows: Sequence[LogRow], end_us: int, *tests: Callable[[Any], bool]) -> list[float]:
    """For each test, the seconds the held value passed it (each update holds until the next, the last until the log ends)."""
    if not rows:
        return [0.0 for _ in tests]
    ends = [r[0] for r in rows[1:]]
    ends.append(end_us)
    return [sum(max(0, nxt - t) for (t, v), nxt in zip(rows, ends, strict=True) if test(v)) / US_PER_S for test in tests]


def _messages(rows: TopicRows, names: list[str], origin: int) -> tuple[dict[str, Any], list[str]]:
    if not names:
        return {"checked": False, "why": "no text topics"}, []
    errors: list[tuple[float, str]] = []
    warnings: list[tuple[float, str]] = []
    overruns: list[float] = []
    repeats: Counter[str] = Counter()
    first_text: dict[str, str] = {}
    for name in names:
        for t, v in rows[name]:
            text = v if isinstance(v, str) else str(v)
            low, at = text.lower(), round((t - origin) / US_PER_S, 1)
            if "overrun" in low:
                overruns.append(at)
            elif _ERROR_WORD.search(low):
                errors.append((at, text))
            elif "warn" in low:
                warnings.append((at, text))
            else:
                continue
            key = _NUMBER.sub("#", text)[:80]
            repeats[key] += 1
            first_text.setdefault(key, text[:100])
    errors.sort()
    warnings.sort()
    out: dict[str, Any] = {"errors": len(errors), "warnings": len(warnings), "overruns": len(overruns)}
    if errors:
        out["first_errors"] = [[t, s[:120]] for t, s in errors[:MAX_EXAMPLES]]
    if warnings:
        out["first_warnings"] = [[t, s[:120]] for t, s in warnings[:MAX_EXAMPLES]]
    if overruns:
        out["first_overrun_s"] = overruns[0]
    top = [[n, first_text[key]] for key, n in repeats.most_common(MAX_EXAMPLES) if n >= 5]
    if top:
        out["repeated"] = top  # the same line again and again: a noisy fault, not many different ones
    flags = []
    if errors:
        flags.append(f"high: {len(errors)} error(s); first at {errors[0][0]} s: {errors[0][1][:80]}")
    if overruns:
        flags.append(f"med: {len(overruns)} loop overrun message(s); first at {overruns[0]} s")
    if warnings:
        flags.append(f"low: {len(warnings)} warning(s); first at {warnings[0][0]} s: {warnings[0][1][:80]}")
    return out, flags


def _battery(end_us: int, rows: TopicRows, battery_pick: Choice, browned_pick: Choice, origin: int) -> tuple[dict[str, Any], list[str]]:
    flags: list[str] = []
    out: dict[str, Any] = {}
    battery: Topic | None = battery_pick.topics[0] if battery_pick.topics else None
    browned = browned_pick.topics
    if battery:
        every = _numbers(rows, battery)
        readings = [(t, v) for t, v in every if float(v) > 0]  # 0 V is no battery (bench or simulation), not a brownout
        if not every:
            out["battery"] = {"checked": False, "topic": battery.name, "why": "the battery topic has no numeric readings"}
        elif readings:
            at_us, low = min(readings, key=lambda r: r[1])
            at = round((at_us - origin) / US_PER_S, 1)
            under, brown = _held_s(every, end_us, lambda v: 0 < v < LOW_VOLTS, lambda v: 0 < v < BROWNOUT_VOLTS)  # a 0 V stretch is not sag
            out["battery"] = {"topic": battery.name, "chosen_by": battery_pick.by, "min_v": round(low, 2), "min_at_s": at, "below_10v_s": round(under, 1), "below_7v_s": round(brown, 1)}
            if battery_pick.others:
                out["battery"]["others"] = battery_pick.others  # other topics that could be the battery: confirm which one is
            if low < BROWNOUT_VOLTS:
                flags.append(f"high: battery fell to {low:.1f} V at {at} s (the roboRIO can brown out below 7 V)")
            elif low < LOW_VOLTS:
                flags.append(f"med: battery fell to {low:.1f} V at {at} s; {under:.1f} s below 10 V")
        else:
            out["battery"] = {"checked": False, "topic": battery.name, "why": "battery reads 0 V (no battery connected?)"}
    else:
        out["battery"] = {"checked": False, "why": "no topic follows a battery name (a leaf BatteryVoltage, or Voltage under PowerDistribution, PDH, PDP or Battery)"}
        if battery_pick.others:
            out["battery"] |= {"candidates": battery_pick.others, "hint": "not used, a name is not proof; pass battery=<topic> to scan with one"}
    seen = {}
    for topic in browned:
        flag_rows = [r for r in rows[topic.name] if isinstance(r[1], bool)]
        if (secs := _held_s(flag_rows, end_us, bool)[0]) > 0:
            seen[topic.name] = round(secs, 1)
    if seen:
        out["brownout_s"] = seen
        flags.append(f"high: brownout flag true for {max(seen.values())} s ({next(iter(seen))})")
    elif browned:
        out["brownout_s"] = 0
    elif browned_pick.others:
        out["brownout_candidates"] = browned_pick.others  # named like a brownout flag but not a known one, so not used
    return out, flags


def _link(rows: TopicRows, links: dict[str, list[Topic]]) -> tuple[dict[str, Any], list[str]]:
    out: dict[str, Any] = {}
    used: dict[str, str] = {}
    flags: list[str] = []
    for label, _, limit in LINK:
        if not links[label]:
            continue
        topic = links[label][0]
        if values := _numbers(rows, topic):
            peak = max(float(v) for _, v in values)
            if label in FRACTION_LABELS and topic.unit != "%" and peak <= 1.0 and "systemstats" in topic.name.lower():
                peak *= 100  # only SystemStats names are read as fractions: a unit-less percent below 1 stays as logged
            out[label] = round(peak, 1)
            used[label] = topic.name
            if peak > limit:
                flags.append(f"med: {label} peaked at {round(peak, 1)} ({topic.name})")
    if not out:
        out["checked"] = False
        out["why"] = "no Driver Station or SystemStats link topics"
    else:
        out["from"] = used  # these are found by name, so say which topic each number came from
    return out, flags


def _silent(log: LogLike, visible: Visible, origin: int) -> tuple[list[list[Any]], list[str]]:
    """Busy topics whose last update came well before the log ended."""
    limit_us = max(MIN_SILENT_S, log.duration_s * 0.1) * US_PER_S
    stopped = sorted((t for t, _ in visible if (t.is_numeric or _is_bool(t)) and t.count >= MIN_SILENT_UPDATES and t.last_us is not None
                      and log.end_us - t.last_us > limit_us), key=lambda t: -t.count)
    rows = [[t.name, round((t.last_us - origin) / US_PER_S, 1)] for t in stopped[:MAX_EXAMPLES]]  # type: ignore[operator]
    if not rows:
        return rows, []
    if len(stopped) >= MANY_STOPPED:  # a test sequence that finished, or an idle subsystem, stops many signals together: not that many faults
        return rows, [f"low: {len(stopped)} busy signals stopped early (e.g. {rows[0][0]} at {rows[0][1]} s); this many together is usually a finished test sequence or an idle subsystem"]
    return rows, [f"med: {len(stopped)} busy signal(s) stopped early, e.g. {rows[0][0]} at {rows[0][1]} s"]


@serialized
def scan(log: LogLike, battery_topic: str | None = None) -> dict[str, Any]:
    """The usual trouble in a log: `flags` ranked high to low, then the numbers behind them. Times are seconds after the first
    autonomous (else the first enable, else the log start; `t0` says which). `battery_topic` names the battery voltage topic;
    without it only a topic with a conventional name is used (see signal_roles.py)."""
    visible: Visible = [(t, t.name.lower()) for t in log.topics.values() if not t.hidden]
    text_names = [n for n in text_topics(log) if n in log.topics]
    shown = [t for t, _ in visible]
    battery = battery_choice(shown, battery_topic, log.topics)
    browned = brownout_choice(shown)
    links = {label: _pick(visible, *words)[:1] for label, words, _ in LINK}
    full_loop, user_loop, loop_names = loop_topics(shown)
    buses = counter_topics(shown)
    loop_used = [t for t in (full_loop, user_loop) if t is not None]
    swerve = log_pair(log)
    swerve_used = [swerve.measured, swerve.setpoint] if swerve.measured and swerve.setpoint else []
    wanted = [*text_names, *(t.name for t in battery.topics), *(t.name for t in browned.topics), *(t.name for found in links.values() for t in found),
              *(t.name for t in loop_used), *(t.name for fields in buses.values() for t in fields.values()), *swerve_used]
    rows = log.rows(list(dict.fromkeys(wanted)))  # one pass over the file for every topic the checks read
    found = phases(log)
    align = "auto" if any(p.mode == "auto" for p in found) else "enable" if found else "start"
    origin, label = origin_us(log, align)
    out: dict[str, Any] = {"t0": label, "duration_s": round(log.duration_s, 1), "enabled_periods": len(found)}
    flags: list[str] = []
    if log.cut_short:
        flags.append("med: the log ends mid-record (cut short, e.g. by a power loss); later data is missing")
    if len(found) > MANY_ENABLES:
        flags.append(f"med: the robot was enabled {len(found)} times (repeated enable/disable or mode changes)")
    if not found:
        flags.append("low: no Driver Station data, so the robot's enabled periods are unknown")
    out["messages"], more = _messages(rows, text_names, origin)
    flags += more
    power, more = _battery(log.end_us, rows, battery, browned, origin)
    out.update(power)
    flags += more
    out["link"], more = _link(rows, links)
    flags += more
    out["loop"], more = loop_check(rows, full_loop, user_loop, loop_names, found)
    flags += more
    if swerve_used:
        out["swerve"], more = scan_block(rows, swerve, analysis_windows(found, log.start_us, log.end_us, None, None), origin)
        flags += more
    can_rows, more = can_check(rows, buses, found)
    if can_rows:
        out["can_bus"] = can_rows
    flags += more
    silent, more = _silent(log, visible, origin)
    if silent:
        out["stopped_early"] = silent
    flags += more
    order = {"high": 0, "med": 1, "low": 2}
    out["flags"] = sorted(flags, key=lambda f: (order[f.split(":", 1)[0]], "brownout" not in f)) or ["none found by these checks"]  # a brownout leads its tier
    out["not_checked"] = "wiring, mechanisms and signals not named above: use wpilog_query"
    return out

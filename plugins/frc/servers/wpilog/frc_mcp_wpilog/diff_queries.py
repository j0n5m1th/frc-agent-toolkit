"""`diff`: what was set differently in two logs (topics that never changed, so they read as settings)."""

from typing import Any, NamedTuple

from frc_mcp_wpilib.conditions import values_equal
from frc_mcp_wpilib.values import DEFAULT_PRECISION, shape

from .code_version import code_version
from .logfile import PRELOAD_ROWS_PER_TOPIC, serialized
from .loglike import LogLike

MAX_SETTING_UPDATES = PRELOAD_ROWS_PER_TOPIC  # a topic busier than this is a signal, not a setting


class _Settings(NamedTuple):
    """What one log holds that looks like a setting: topics whose value never changed (and what it was)."""

    values: dict[str, tuple[str, Any]]  # topic -> (its NT4 type, its shaped value)
    varying: set[str]  # topics that changed during the log, or are too busy to be a setting
    busy: int  # how many matching topics were skipped for being busier than a setting can be


def _settings(log: LogLike, prefix: str, needle: str) -> _Settings:
    """The never-changing topics of a log that match the filter: its tunables, constants and match info.

    A signal changes all the time, so comparing it between logs says nothing; a setting is what a team changes on
    purpose. Topics with more updates than `MAX_SETTING_UPDATES` are signals by nature, so they are not even read."""
    log.activate_schemas()
    names, busy = [], 0
    varying: set[str] = set()
    for topic in log.topics.values():
        if topic.hidden or not topic.name.startswith(prefix) or needle not in topic.name.lower():
            continue
        if topic.count > MAX_SETTING_UPDATES:
            busy += 1
            varying.add(topic.name)
        elif topic.count:
            names.append(topic.name)
    values: dict[str, tuple[str, Any]] = {}
    for name, rows in log.rows(names).items():
        if not rows:  # every record of the topic failed to decode: nothing to compare
            continue
        first = rows[0][1]
        if all(values_equal(first, value) for _, value in rows):
            type_str = log.topics[name].type_str
            values[name] = (type_str, shape(first, type_str, None, DEFAULT_PRECISION))
        else:
            varying.add(name)
    return _Settings(values, varying, busy)


def _capped(items: dict[str, Any] | list[str], limit: int, omitted: dict[str, int], section: str) -> Any:
    """The first `limit` of a section's items, noting in `omitted` how many were left out."""
    if len(items) <= limit:
        return items
    omitted[section] = len(items) - limit
    return dict(list(items.items())[:limit]) if isinstance(items, dict) else items[:limit]


def _difference(first_id: str, first: tuple[str, Any], second_id: str, second: tuple[str, Any]) -> dict[str, Any]:
    """One changed setting: its value in each log, and its type in each when the two logs declare different types."""
    out: dict[str, Any] = {first_id: first[1], second_id: second[1]}
    if first[0] != second[0]:
        out["types"] = {first_id: first[0], second_id: second[0]}
    return out


@serialized
def diff(logs: dict[str, LogLike], prefix: str, name_contains: str | None, limit: int) -> dict[str, Any]:
    """What was set differently in two logs: `changed` (a setting with another value, or another type, in each),
    `only_in` (a setting one log has a value for and the other does not), and `varied_in` (a setting in one log that
    changed during the other, for example tuned live). `same` counts the settings that match. Needs exactly two logs."""
    (first_id, first), (second_id, second) = logs.items()
    needle = (name_contains or "").lower()
    one, two = _settings(first, prefix, needle), _settings(second, prefix, needle)
    both = sorted(one.values.keys() & two.values.keys())
    changed = {t: _difference(first_id, one.values[t], second_id, two.values[t]) for t in both if one.values[t] != two.values[t]}
    only_in = {
        first_id: sorted(t for t in one.values if t not in two.values and t not in two.varying),
        second_id: sorted(t for t in two.values if t not in one.values and t not in one.varying),
    }
    varied_in = {
        first_id: sorted(two.values.keys() & one.varying),
        second_id: sorted(one.values.keys() & two.varying),
    }
    omitted: dict[str, int] = {}
    out: dict[str, Any] = {"logs": {first_id: first.path.name, second_id: second.path.name}, "same": len(both) - len(changed)}
    if changed:
        out["changed"] = _capped(changed, limit, omitted, "changed")
    for key, section in (("only_in", only_in), ("varied_in", varied_in)):
        kept = {log_id: _capped(topics, limit, omitted, f"{key}.{log_id}") for log_id, topics in section.items() if topics}
        if kept:
            out[key] = kept
    code_a, code_b = code_version(first)[0], code_version(second)[0]
    if code_a != code_b:  # whatever the filter, a reader must know the two logs come from different code
        differing = sorted(key for key in code_a.keys() | code_b.keys() if code_a.get(key) != code_b.get(key))
        out["code"] = {first_id: {k: code_a.get(k) for k in differing}, second_id: {k: code_b.get(k) for k in differing}}
        out["warning"] = "These logs come from different code (or one records no build facts), so a changed value may be a code change, not tuning."
    if one.busy or two.busy:
        out["signals_skipped"] = {first_id: one.busy, second_id: two.busy}
    if omitted:
        out["omitted"] = omitted
    return out

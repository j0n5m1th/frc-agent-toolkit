"""Collapse a "sendable" topic group (a base path with `.type` and child topics) into one value.
A WPILib chooser is /X/.type ("Selectable" or "String Chooser"), /X/options, /X/default, /X/active, /X/selected (some publishers
nest /X/selected/value), and becomes one object. Anything else (Field2d, Mechanism2d, ...) becomes {type, v: {relative_name: value}}.
"""

from typing import Any, NamedTuple, TypedDict

from .reduce.types import UpdateAge
from .values import (
    UNSET,  # the shared "not published" marker
    JsonValue,
    Unset,
)

CHOOSER_TYPES = {"String Chooser", "Selectable"}
_CHOOSER_KEYS = ("options", "default", "active", "selected", "selected/value", "selected/tune")


class _Chooser(NamedTuple):
    """The children of a chooser group; a part that is not published is `UNSET`."""

    options: Unset | JsonValue
    default: Unset | JsonValue
    active: Unset | JsonValue
    selected: Unset | JsonValue
    selected_value: Unset | JsonValue
    selected_tune: Unset | JsonValue

    @classmethod
    def read(cls, vals: dict[str, Any]) -> "_Chooser":
        return cls(*(vals.get(key, UNSET) for key in _CHOOSER_KEYS))


def _or_none(part: Unset | JsonValue) -> JsonValue:
    return None if isinstance(part, Unset) else part  # the marker has exactly one instance, so this matches `is`


def _resolve_choice(candidates: tuple[tuple[str, Any, bool], ...]) -> tuple[tuple[str, Any] | None, bool]:
    """First usable (source, value) in priority order, and whether a higher-priority value is unavailable."""
    for source, value, missing in candidates:
        if missing:
            return None, True
        if value is not None and value != "":
            return (source, value), False
    return None, False


class ChooserVerdict(TypedDict, total=False):
    """The `current` / `warning` tail of a chooser reply: one key per verdict, none when there is nothing to say."""

    current: str
    warning: str


class _GroupReplyBase(TypedDict, total=False):
    """Keys both widget shapes carry; `build_widget_group` is the only writer of all of them."""

    truncated: bool
    unavailable: dict[str, dict[str, str]]
    last_updates: dict[str, UpdateAge]  # key -> the age record `clock.update_age` builds


class ChooserReply(_GroupReplyBase, total=False):
    """A collapsed chooser: only the children the group publishes are present (`input` names the selected source)."""

    type: str
    options: JsonValue
    default: JsonValue
    active: JsonValue
    input: str
    selected: JsonValue
    current: str
    warning: str


class PlainGroupReply(_GroupReplyBase, total=False):
    """A collapsed non-chooser group: its `.type` (when published) and every child value by relative name."""

    type: str
    v: dict[str, JsonValue]


def _chooser_verdict(options: Any, truncated: bool, unknown: bool, choice: tuple[str, Any] | None) -> ChooserVerdict:
    """The `current` / `warning` part of a chooser reply, as two independent facts.
    `current` is whatever the priority order resolved to a plain string, so a published choice survives an incomplete option
    list. `warning` says what the data cannot confirm: bad or missing `options`, an unavailable higher-priority value, or a choice
    outside the list. Both can appear together.
    """
    current = choice[1] if choice is not None and isinstance(choice[1], str) else None
    out: ChooserVerdict = {} if current is None else {"current": current}
    if truncated or not isinstance(options, list) or not all(isinstance(v, str) for v in options):
        out["warning"] = "Incomplete chooser data; current choice is unknown."
    elif unknown:
        out["warning"] = "A higher-priority chooser value is unavailable; current choice is unknown."
    elif choice is not None and (current is None or current not in options):
        # `current` is missing here only when the resolved value was not a string (a number, a bool, ...).
        source, _ = choice
        out["warning"] = f"{source} is not a valid listed option; the robot's choice is unknown."
    return out


def _build_chooser(
    typ: Any, vals: dict[str, Any], truncated: bool, unavailable: dict[str, dict[str, str]]
) -> ChooserReply:
    parts = _Chooser.read(vals)
    out: ChooserReply = {"type": typ}
    if parts.options is not UNSET:
        out["options"] = _or_none(parts.options)
    if parts.default is not UNSET:
        out["default"] = _or_none(parts.default)
    if parts.active is not UNSET:
        out["active"] = _or_none(parts.active)
    if parts.selected_tune is not UNSET:  # a dashboard publishes its choice here; the robot echoes it into selected/value
        out["input"] = "selected/tune"
    selected = _or_none(parts.selected if parts.selected is not UNSET else parts.selected_value)
    if selected is not None:
        out["selected"] = selected
    selected_unknown = "selected" in unavailable or (
        parts.selected is UNSET and "selected/value" in unavailable
    )
    choice, unknown = _resolve_choice((
        ("selected", selected, selected_unknown),
        ("active", _or_none(parts.active), "active" in unavailable),
        ("default", _or_none(parts.default), "default" in unavailable),
    ))
    out.update(_chooser_verdict(_or_none(parts.options), truncated, unknown, choice))
    return out


def build_widget_group(
    rel_vals: dict[str, Any],
    truncated: bool = False,
    unavailable: dict[str, dict[str, str]] | None = None,
    last_updates: dict[str, Any] | None = None,
) -> ChooserReply | PlainGroupReply:
    """The one widget reply, from `rel_vals`: child path relative to the base ('_' = the base topic itself) -> shaped value.

    `unavailable` maps those same keys to their per-key miss records, `last_updates` to their age records; each section
    is written only when it has entries, so this module stays the single producer of the reply shape."""
    typ = rel_vals.get(".type")
    # deeper `.type` entries (nested widgets) are noise once the base type is known
    vals = {(k or "_"): v for k, v in rel_vals.items() if k != ".type" and not k.endswith("/.type")}
    out: ChooserReply | PlainGroupReply
    if typ in CHOOSER_TYPES:
        # same dict the reply echoes below; membership tests read its keys
        out = _build_chooser(typ, vals, truncated, unavailable or {})
    else:
        plain: PlainGroupReply = {"v": vals}
        if typ is not None:
            plain = {"type": typ, **plain}
        out = plain
    if truncated:
        out["truncated"] = True
    if unavailable:
        out["unavailable"] = unavailable
    if last_updates:
        out["last_updates"] = last_updates
    return out


__all__ = ["CHOOSER_TYPES", "ChooserReply", "JsonValue", "PlainGroupReply", "build_widget_group"]

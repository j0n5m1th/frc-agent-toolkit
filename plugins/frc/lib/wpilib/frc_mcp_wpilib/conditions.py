"""Conditions on a topic value, shared by nt4_wait_for and capture triggers."""

import math
import operator
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .errors import WPILibMCPError
from .schema.structs import SchemaError
from .values import (
    UNSET,  # noqa: F401  (re-exported marker)
    Unset,  # noqa: F401  (re-exported type)
    decode_struct_strict,
    to_bool,
)

CompareOp = Literal["==", "!=", ">", ">=", "<", "<=", "changed"]
WaitOp = CompareOp | Literal["exists"]  # the ops a wait (nt4_wait_for) or a condition check accepts

_ORDER = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


class StructDecodePending(WPILibMCPError):
    """A struct value cannot yet be decoded and a later update may be usable."""


class ConditionIndeterminate(WPILibMCPError):
    """A value cannot establish whether a condition holds."""


def _contains_nan(value: Any) -> bool:
    if isinstance(value, float):
        return math.isnan(value)
    if isinstance(value, Decimal):
        return value.is_nan()
    if isinstance(value, (list, tuple)):
        return any(_contains_nan(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_nan(item) for item in value.values())
    return False


def values_equal(left: Any, right: Any) -> bool:
    """Treat repeated NaNs as the same observed value, including in arrays and structs.

    One container rule for both helpers here: a sequence is a `list` or a `tuple` (as `_contains_nan` sees it) and
    compares by length and items, so the sequence kind itself is not part of the comparison."""
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(values_equal(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(values_equal(value, right[key]) for key, value in left.items())
    return left == right


def extract(type_str: str, raw: Any, field: str | None) -> Any:
    """The value a condition compares: the raw value, or one decoded struct field."""
    if field is None:
        return raw
    decoded = None
    if isinstance(raw, (bytes, bytearray)):
        try:
            decoded = decode_struct_strict(type_str, bytes(raw))
        except SchemaError as e:
            if type_str.startswith(("struct:", "proto:")):
                raise StructDecodePending(f"Cannot decode {type_str!r} yet: {e}") from e
            raise WPILibMCPError(f"`field` needs a decodable struct topic; {type_str!r} is not one (yet).") from e
    if not isinstance(decoded, dict):
        raise WPILibMCPError(f"`field` needs a decodable struct topic; {type_str!r} is not one (yet).")
    for k, v in decoded.items():
        if k == field or k.rsplit(".", 1)[-1] == field:
            return v
    raise WPILibMCPError(f"Struct has no field {field!r}; fields: {sorted(decoded)}.")


def _coerce(target: Any, like: Any) -> Any:
    if isinstance(like, bool):
        return to_bool(target)
    if isinstance(like, int):
        if isinstance(target, (int, float, Decimal)):
            return target
        if isinstance(target, str):
            return Decimal(target)
        return float(target)
    if isinstance(like, float):
        return float(target)
    if isinstance(like, str):
        return str(target)
    return target


# Naming convention (see config.py): `check_*` validate and return None; `checked_*` return the normalized value.
def check_compare_target(op: WaitOp, target: Any) -> None:
    """Raise WPILibMCPError when `op` compares against a value but none was given (used by `wait_for`, `holds()` and the capture
    trigger and event-rule validators).
    """
    if op not in ("exists", "changed") and target is None:
        raise WPILibMCPError(f"op {op!r} needs a `value` to compare against.")


def validate_compare_target(op: WaitOp, target: Any) -> None:
    """`check_compare_target` for pydantic validators: a plain ValueError, so FastMCP shows the message while it masks other errors."""
    try:
        check_compare_target(op, target)
    except WPILibMCPError as exc:
        raise ValueError(str(exc)) from None


def holds(op: WaitOp, cur: Any, target: Any, baseline: Any) -> bool:
    if op == "exists":
        return True
    if op == "changed":
        return not values_equal(cur, baseline)
    check_compare_target(op, target)
    try:
        if _contains_nan(cur):
            raise ConditionIndeterminate("A NaN value cannot establish a comparison; waiting for a valid value.")
        if _contains_nan(target):
            raise WPILibMCPError("A NaN comparison value cannot establish a condition.")
        t = _coerce(target, cur)
        if _contains_nan(t):
            raise WPILibMCPError("A NaN comparison value cannot establish a condition.")
        if op == "==":
            return cur == t
        if op == "!=":
            return cur != t
        if isinstance(cur, int) and not isinstance(cur, bool):
            a: float = cur
            b: float = t
        else:
            a, b = float(cur), float(t)  # ordering needs numbers (bool counts as 0/1)
        return _ORDER[op](a, b)
    except (TypeError, ValueError, OverflowError, InvalidOperation) as e:
        raise WPILibMCPError(f"Cannot compare {cur!r} {op} {target!r}: {e}") from e

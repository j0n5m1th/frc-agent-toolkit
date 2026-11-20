"""Fields every topic of a reduced reply has with the same value, stated once in the reply's `all` block."""

from typing import Any

SHARED_KEYS = ("capture_rate_hz", "sampling")  # per-topic fields that move to the reply's `all` block when every topic agrees


def hoist_shared(data: dict[str, Any], keys: tuple[str, ...] = SHARED_KEYS) -> dict[str, Any]:
    """Move fields that every topic result has, with one value, out of `data` and return them (the reply's `all` block).

    Strict by design, so `all` never claims something a topic does not have: at least two topics, each with the field,
    all equal. Otherwise the field stays on each topic and nothing is returned for it."""
    shared: dict[str, Any] = {}
    if len(data) < 2 or not all(isinstance(entry, dict) for entry in data.values()):
        return shared
    for key in keys:
        values = [entry[key] for entry in data.values() if key in entry]
        if len(values) == len(data) and all(value == values[0] for value in values):
            shared[key] = values[0]
    for key in shared:
        for entry in data.values():
            del entry[key]
    return shared

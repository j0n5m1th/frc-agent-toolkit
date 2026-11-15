"""Listener for WPILib `/.schema/` topics: hands each value to `frc_mcp_wpilib/schema/state.py`. The session attaches
`on_schema` on connect and empties the schemas through `state.forget` on disconnect.
"""

import logging

import ntcore

from frc_mcp_wpilib.schema import state

_log = logging.getLogger(__name__)


def on_schema(ev: ntcore.Event) -> None:
    """Hand one schema value to the registries. Never raises: it runs on an ntcore thread, and a malformed value from the
    robot must not escape it."""
    try:
        data = ev.data
        raw = data.value.value()
        if not isinstance(raw, (bytes, bytearray)):
            return
        state.add(data.topic.getName(), bytes(raw))
    except Exception as e:  # noqa: BLE001 - any failure is the robot's data being unusable
        _log.debug("ignored a schema value that could not be added: %r", e)

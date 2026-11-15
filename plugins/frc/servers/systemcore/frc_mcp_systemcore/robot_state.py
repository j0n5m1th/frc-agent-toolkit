"""Is the robot enabled? The guard before restarting robot code or rebooting.
Reads `/DriverStation/ControlWord` (a WPILib struct) once from the robot program's NetworkTables server on port 5810, as a plain
client: nothing is published and port 1740 (the Driver Station's single slot) is never connected to. With no value the state is
"unknown". `blockers` looks only at enabled and FMS; `restart.guard` decides what to do about unknown.
"""

import asyncio
import time
from typing import Any

from frc_mcp_wpilib.schema import structs

from . import net

IDENTITY = "mcp-systemcore"
CONTROL_WORD = "/DriverStation/ControlWord"
SCHEMA = "/.schema/struct:ControlWord"
BLOCKING = ("enabled", "fmsAttached")  # a robot that is enabled, or run by the field, is never restarted from here


def control_word_sync(host: str, wait_s: float = 3.0) -> dict[str, Any] | None:
    """The ControlWord fields (`enabled`, `eStop`, `fmsAttached`, `dsAttached`, ...), or None if they cannot be read within `wait_s`."""
    import ntcore  # here, not at the top: it is only needed when a restart or reboot asks whether the robot is enabled

    instance = ntcore.NetworkTableInstance.create()
    try:
        instance.startClient4(IDENTITY)
        instance.setServer(host, net.port("nt"))
        word = instance.getTopic(CONTROL_WORD).genericSubscribe()
        schema = instance.getTopic(SCHEMA).genericSubscribe()
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            value, text = word.get(), schema.get()
            if value.isValid() and text.isValid():
                registry = structs.Registry()
                registry.add(SCHEMA, bytes(text.getRaw()).decode("utf-8", "replace"))
                decoded = registry.decode("struct:ControlWord", bytes(value.getRaw()))
                return decoded if isinstance(decoded, dict) else None
            time.sleep(0.05)
        return None
    finally:
        instance.stopClient()
        ntcore.NetworkTableInstance.destroy(instance)


async def control_word(host: str, wait_s: float = 3.0) -> dict[str, Any] | None:
    return await asyncio.to_thread(control_word_sync, host, wait_s)


def blockers(word: dict[str, Any] | None) -> list[str]:
    """What stops a restart: the names of the BLOCKING fields that are set. Empty for an unknown state (restart.guard handles that)."""
    return [name for name in BLOCKING if word and word.get(name)]

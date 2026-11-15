"""What the diagnostic server says, shaped for an assistant: the device list, one device by a friendly name, its signals and
their live values. A device is a `getdevices` entry (`Name` like `0 - DT-GYRO`, `Model` the exact string like `Pigeon 2 vers. S`,
`ID`, `CANbus`, ...). Later questions send its model, id and bus, so `resolve` finds them from whatever was typed.
"""

from dataclasses import dataclass
from typing import Any

from frc_mcp_ctre.errors import CtreError

from . import client, config

FAULT_PREFIXES = ("Fault_", "StickyFault_")  # Phoenix 6 signal names (seen on a Pigeon 2): a fault and its sticky twin
MAX_SIGNALS = 30
_SIGNALS: dict[tuple[str, int, str, str], list[dict[str, Any]]] = {}  # (host, port, model, firmware) -> that model's signal list at that firmware
# (host, port) -> {bus label: `CANbus` value} for every bus a device was listed on in this process. Kept on purpose: seen with the CAN low wire pulled, the device list went
# EMPTY, and a bus with no devices can only be asked about by a name remembered from before, yet its error counters are what matter then.
_BUSES: dict[tuple[str, int], dict[str, str]] = {}


@dataclass(frozen=True)
class Device:
    name: str
    model: str
    id: int
    bus: str  # the `CANbus` value to send back
    label: str  # what to call the bus: the CANivore's name when it has one
    raw: dict[str, Any]

    def query(self) -> dict[str, Any]:
        return {"model": self.model, "id": self.id, "canbus": self.bus}


def num(value: Any) -> Any:
    """A number as short as it can be: 26.0 is 26; 189.23126220703125 is 189.2313."""
    if isinstance(value, float):
        return int(value) if value.is_integer() else round(value, 4)
    return value


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise CtreError(f"The diagnostic server sent {value!r} where a device id (a number) belongs: a version this server does not know.") from e


def as_float(value: Any) -> float | None:
    """A number the server sent, or None when it is missing or not a number."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


async def find_device(host: str, query: str) -> Device:
    """The device a user's name means (see `resolve`), from a fresh device list."""
    _, found = await devices(host)
    return resolve(found, query)


async def devices(host: str) -> tuple[dict[str, Any], list[Device]]:
    reply = await client.call("getdevices", host)
    found = [Device(str(d.get("Name", "")), str(d.get("Model", "")), _int(d.get("ID")), str(d.get("CANbus", "")), str(d.get("CANivoreDevName") or d.get("CANbus", "")), d)
             for d in reply.get("DeviceArray") or [] if isinstance(d, dict)]
    _BUSES.setdefault((host, config.port()), {}).update({d.label: d.bus for d in found})
    return reply, found


def known_buses(host: str) -> dict[str, str]:
    """The buses a device was listed on, earlier or now, as {label: CANbus value}."""
    return dict(_BUSES.get((host, config.port()), {}))


def resolve(found: list[Device], query: str) -> Device:
    """The device a name means: its `Name`, or `<model>#<id>`, or its id or its model when only one device has it. Anything else lists what exists."""
    wanted = query.strip().lower()
    by_name = [d for d in found if d.name.lower() == wanted]
    by_model_id = [d for d in found if f"{d.model}#{d.id}".lower() == wanted]
    by_id = [d for d in found if wanted.isdigit() and d.id == int(wanted)]
    by_model = [d for d in found if d.model.lower() == wanted]
    for matches in (by_name, by_model_id, by_id, by_model):
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(repr(d.name) for d in matches)
            raise CtreError(f"{query!r} matches {len(matches)} devices ({names}). Use a full name from diag_status.")
    seen = ", ".join(repr(d.name) for d in found) or "none"
    raise CtreError(f"The diagnostic server lists no device {query!r}. It lists: {seen}.")


async def signals(host: str, device: Device) -> list[dict[str, Any]]:
    """The device model's signals (`Id`, `Name`, `Units`, `Summary`), asked for once per model."""
    key = (host, config.port(), device.model, str(device.raw.get("CurrentVers")))  # a firmware update can add or rename signals
    if key not in _SIGNALS:
        reply = await client.call("getsignals", host, **device.query())
        _SIGNALS[key] = [s for s in reply.get("Signals") or [] if isinstance(s, dict) and "Id" in s and "Name" in s]
    return _SIGNALS[key]


async def values(host: str, device: Device, ids: list[int]) -> dict[int, Any]:
    """Live values by signal id (a signal the device does not answer for is simply missing)."""
    if not ids:
        return {}
    reply = await client.call("getsignalvalue", host, **device.query(), signals=",".join(str(i) for i in ids))
    return {int(k): v for k, v in (reply.get("Values") or {}).items()}


async def active_faults(host: str, device: Device) -> tuple[list[str], list[str], int]:
    """(faults set now, sticky faults set since power-up, how many fault signals were read), from the device's `Fault_*` and `StickyFault_*` signals."""
    wanted = {s["Id"]: s["Name"] for s in await signals(host, device) if str(s["Name"]).startswith(FAULT_PREFIXES)}
    read = await values(host, device, list(wanted))
    set_names = [str(wanted[i]) for i, v in read.items() if v]
    sticky = [n.removeprefix("StickyFault_") for n in set_names if n.startswith("StickyFault_")]
    return [n.removeprefix("Fault_") for n in set_names if n.startswith("Fault_")], sticky, len(wanted)

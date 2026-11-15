"""Driver Station logs (`.dslog` and `.dsevents`) read as if they were a `.wpilog`.

The pair is turned into WPILOG bytes in memory (nothing is written), so every log tool works on it. Topics are `/DSLog/*`,
`/DSInfo/StartTimeUTC`, `/DSEvents` (text), plus `DS:enabled` and `DS:autonomous` (autonomous = enabled and not teleop; the
log cannot tell autonomous from test).

Format: DSLOG version 4 as read by AdvantageScope (BSD license), which cites the community DSLOG-Reader and frcture notes.
FIRST does not publish it. Written from that description and tested only against generated files, not yet against logs from
a real Driver Station.

Times: both files start with a LabVIEW timestamp; `.dslog` records are 20 ms apart. Time zero is the `.dslog` start (or the
`.dsevents` start when there is no `.dslog`).
"""

import json
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from frc_mcp_wpilib.wpilog_writer import LogWriter

from .errors import WPILogError

DSLOG = ".dslog"
DSEVENTS = ".dsevents"
SUFFIXES = (DSLOG, DSEVENTS)
VERSION = 4
_HEADER = struct.Struct(">iqQ")  # version, LabVIEW seconds, LabVIEW fraction (big-endian)
_EVENT_HEAD = struct.Struct(">qQi")  # LabVIEW seconds, fraction, text length
_LABVIEW_TO_UNIX_S = 2_082_844_800  # seconds from 1904-01-01 to 1970-01-01, both UTC
PERIOD_US = 20_000  # one .dslog record every 20 ms
_BASE = struct.Struct(">BbHBBBBH")  # trip time, packet loss, battery, CPU, status, CAN, Wi-Fi dB, Wi-Fi Mb
_PD_HEADER = 4  # the power distribution section starts with 4 bytes; the last one is its type
_PD_REV, _PD_CTRE = 33, 25
_MAX_BATTERY_V = 20.0  # higher readings (seen at the end of logs) are noise; the last good value is kept
# status byte bits: a 0 bit means the flag is set
_STATUS_BITS = {"Brownout": 7, "Watchdog": 6, "DSTeleop": 5, "DSDisabled": 3, "RobotTeleop": 2, "RobotAuto": 1,
                "RobotDisabled": 0}
_EVENT_TAGS = ("<TagVersion>", "<time>", "<count>", "<flags>", "<Code>", "<location>", "<stack>")
MAX_EVENT_TEXT = 64 * 1024  # a longer text length means the file is damaged
MAX_EVENT_AFTER_START_S = 31 * 86_400  # an event further than this after the session start has a damaged time

_NUMBERS = {  # topic -> (unit, scale applied to the raw field)
    "TripTimeMS": ("ms", 0.5),
    "PacketLoss": ("%", 4.0),
    "BatteryVoltage": ("V", 1 / 256),
    "CPUUtilization": ("%", 0.5),
    "CANUtilization": ("%", 0.5),
    "WifiDb": ("dB", 0.5),
    "WifiMb": ("Mb/s", 1 / 256),
}


def is_ds(path: Path) -> bool:
    return path.suffix.lower() in SUFFIXES


def pair(path: Path) -> tuple[Path | None, Path | None]:
    """The `.dslog` and `.dsevents` of the session `path` belongs to (either may be missing)."""
    found: dict[str, Path | None] = {}
    for suffix in SUFFIXES:
        candidate = path.with_suffix(suffix)
        if not candidate.is_file():  # Windows file names ignore case; elsewhere also try the other spelling
            candidate = path.with_suffix(suffix.upper())
        found[suffix] = candidate if candidate.is_file() else None
    return found[DSLOG], found[DSEVENTS]


def canonical(path: Path) -> Path:
    """The one name a session is known by: its `.dslog`, or its `.dsevents` when there is no `.dslog`."""
    dslog, events = pair(path)
    return dslog or events or path


def _labview_s(seconds: int, fraction: int) -> float:
    return seconds - _LABVIEW_TO_UNIX_S + fraction / 2**64


def _check_header(data: bytes, name: str) -> float:
    """The start time (Unix seconds) of a `.dslog` or `.dsevents`; raises WPILogError for a bad header."""
    if len(data) < _HEADER.size:
        raise WPILogError(f"{name} is too short to be a Driver Station log.")
    version, seconds, fraction = _HEADER.unpack_from(data)
    if version != VERSION:
        raise WPILogError(f"{name} is Driver Station log version {version}; only version {VERSION} can be read.")
    start_s = _labview_s(seconds, fraction)
    try:
        datetime.fromtimestamp(start_s, UTC)
    except (OverflowError, OSError, ValueError) as e:  # a damaged time field: the OS rejects it (the error type depends on it)
        raise WPILogError(f"{name} has a start time that is not a real date, so it is probably damaged.") from e
    return start_s


@dataclass
class Converted:
    """A session turned into WPILOG bytes, the files it came from, and whether either file ended mid-record."""

    data: bytes
    sources: list[Path]
    start_unix_us: int  # wall-clock time of the converted log's time zero
    cut_short: bool = False


@dataclass
class _Topic:
    entry: int
    last: Any = None
    last_us: int = 0


@dataclass
class _Out:
    """A LogWriter that only records a value when it changed (a 50 Hz log is mostly repeats)."""

    writer: LogWriter = field(default_factory=LogWriter)
    topics: dict[str, _Topic] = field(default_factory=dict)

    def put(self, name: str, type_str: str, value: Any, t_us: int, unit: str = "") -> None:
        topic = self.topics.get(name)
        if topic is None:
            metadata = json.dumps({"unit": unit, "source": "DriverStation"}) if unit else '{"source": "DriverStation"}'
            topic = self.topics[name] = _Topic(self.writer.start(name, type_str, metadata, t_us))
        if topic.last is None or topic.last != value:
            self.writer.append(topic.entry, value, t_us)
            topic.last = value
            topic.last_us = t_us

    def close(self, t_us: int) -> None:
        """Repeat each topic's last value at `t_us`, so the log lasts as long as the session and holds are measured."""
        for name, topic in self.topics.items():
            if name.startswith(("/DSLog/", "DS:")) and topic.last is not None and topic.last_us < t_us:
                self.writer.append(topic.entry, topic.last, t_us)


def _pd_currents(data: bytes, at: int) -> tuple[list[float], int] | None:
    """The channel currents at `at` (just past the 4-byte section header) and the bytes they took; None if cut off."""
    kind = data[at - 1]
    if kind == _PD_REV:
        size, bits_len, channels, spacing = 33, 27, 20, (3, 32)
    elif kind == _PD_CTRE:
        size, bits_len, channels, spacing = 25, 21, 16, (6, 64)
    else:
        return [], 0
    if at + size > len(data):
        return None
    bits = int.from_bytes(data[at + 1:at + 1 + bits_len], "little")  # bit k of the stream is bit k%8 of byte k//8
    per_group, group_bits = spacing
    width = 10 if kind == _PD_REV else 8  # AdvantageScope reads 8 of the 10 bits per PDP channel; kept as it does
    currents = []
    for i in range(channels):
        start = (i // per_group) * group_bits + (i % per_group) * 10
        currents.append(((bits >> start) & ((1 << width) - 1)) / 8)
    if kind == _PD_REV:
        extra = at + 1 + bits_len
        currents += [b / 16 for b in data[extra:extra + 4]]
    return currents, size


def _read_dslog(data: bytes, out: _Out) -> bool:
    """Write every record of a `.dslog` into `out`; True when the file ends mid-record."""
    at = _HEADER.size
    t_us = 0
    battery = 0.0
    cut_short = False
    while at < len(data):
        pd = _pd_currents(data, at + _BASE.size + _PD_HEADER) if at + _BASE.size + _PD_HEADER <= len(data) else None
        if pd is None:
            cut_short = True
            break
        trip, loss, volts, cpu, status, can, wifi_db, wifi_mb = _BASE.unpack_from(data, at)
        currents, used = pd
        at += _BASE.size + _PD_HEADER + used
        if volts / 256 <= _MAX_BATTERY_V:
            battery = volts / 256
        raw = {"TripTimeMS": trip, "PacketLoss": min(max(loss, 0), 25), "CPUUtilization": cpu,
               "CANUtilization": can, "WifiDb": wifi_db, "WifiMb": wifi_mb}
        for name, value in raw.items():
            unit, scale = _NUMBERS[name]
            out.put(f"/DSLog/{name}", "double", round(value * scale, 4), t_us, unit)
        out.put("/DSLog/BatteryVoltage", "double", round(battery, 4), t_us, "V")
        flags = {name: not status & (1 << bit) for name, bit in _STATUS_BITS.items()}
        for name, value in flags.items():
            out.put(f"/DSLog/Status/{name}", "boolean", value, t_us)
        enabled = not flags["DSDisabled"]
        out.put("DS:enabled", "boolean", enabled, t_us)
        out.put("DS:autonomous", "boolean", enabled and not flags["DSTeleop"], t_us)
        if currents:
            out.put("/DSLog/PowerDistributionCurrents", "double[]", currents, t_us, "A")
        t_us += PERIOD_US
    out.close(max(0, t_us - PERIOD_US))
    return cut_short


def clean_event(text: str) -> str:
    """An event's text without the Driver Station's markup tags (`<time> 1.2`, `<message> `, ...)."""
    for tag in _EVENT_TAGS:
        while (start := text.find(tag)) >= 0:
            end = text.find("<", start + len(tag))
            text = text[:start] + (text[end:] if end >= 0 else "")
    return text.replace("<message> ", "").replace("<details> ", "").strip()


def _read_events(data: bytes, start_s: float, out: _Out) -> bool:
    """Write every event of a `.dsevents` into `out` (times since `start_s`); True when the file ends mid-record."""
    at = _HEADER.size
    found: list[tuple[int, str]] = []
    cut_short = False
    while at < len(data):
        if at + _EVENT_HEAD.size > len(data):
            cut_short = True
            break
        seconds, fraction, length = _EVENT_HEAD.unpack_from(data, at)
        at += _EVENT_HEAD.size
        if length < 0 or length > MAX_EVENT_TEXT or at + length > len(data):
            cut_short = True
            break
        text = clean_event(data[at:at + length].decode("utf-8", errors="replace"))
        at += length
        if abs(_labview_s(seconds, fraction) - start_s) > MAX_EVENT_AFTER_START_S:  # far before or after the session (or not finite): damaged
            cut_short = True
            break
        if text:
            found.append((max(0, round((_labview_s(seconds, fraction) - start_s) * 1e6)), text))
    for t_us, text in sorted(found, key=lambda event: event[0]):  # the log tools expect each topic in time order
        out.put("/DSEvents", "string", text, t_us)
        out.topics["/DSEvents"].last = None  # the same message twice is two events
    return cut_short


def convert(path: Path) -> Converted:
    """The Driver Station session `path` belongs to, as WPILOG bytes. Raises WPILogError for an unreadable file."""
    dslog, events = pair(path)
    sources = [p for p in (dslog, events) if p is not None]
    if not sources:
        raise WPILogError(f"No Driver Station log named {path.name!r}.")
    out = _Out()
    cut_short = False
    blobs = {p: p.read_bytes() for p in sources}
    start_s = _check_header(blobs[sources[0]], sources[0].name)
    out.put("/DSInfo/StartTimeUTC", "string", datetime.fromtimestamp(start_s, UTC).strftime("%Y-%m-%d %H:%M:%S UTC"), 0)
    if dslog is not None:
        cut_short |= _read_dslog(blobs[dslog], out)
    if events is not None:
        _check_header(blobs[events], events.name)
        cut_short |= _read_events(blobs[events], start_s, out)
    return Converted(out.writer.to_bytes(), sources, round(start_s * 1e6), cut_short)

"""Builds small Driver Station logs (`.dslog` + `.dsevents`) for the tests: the inverse of `frc_mcp_wpilog.dslog`."""

import struct
from pathlib import Path

LABVIEW_TO_UNIX_S = 2_082_844_800
START_UNIX_S = 1_710_616_425  # 2024-03-16 19:13:45 UTC


def header(start_unix_s: float = START_UNIX_S, version: int = 4) -> bytes:
    whole = int(start_unix_s)
    return struct.pack(">iqQ", version, whole + LABVIEW_TO_UNIX_S, int((start_unix_s - whole) * 2**64))


def status(*, disabled: bool = True, teleop: bool = False, brownout: bool = False) -> int:
    """The status byte: every bit is 1 (flag off) except the flags that are set."""
    mask = 0xFF
    if brownout:
        mask &= ~(1 << 7)
    if teleop:
        mask &= ~(1 << 5)
        mask &= ~(1 << 2)
    if disabled:
        mask &= ~(1 << 3)
        mask &= ~(1 << 0)
    elif not teleop:
        mask &= ~(1 << 1)
    return mask


def rev_pd(currents: list[float]) -> bytes:
    """A REV PDH section: 4-byte header ending in type 33, CAN id, 27 bytes of 10-bit channels, 4 extra, 1 spare."""
    bits = 0
    for i, amps in enumerate(currents[:20]):
        bits |= (round(amps * 8) & 0x3FF) << ((i // 3) * 32 + (i % 3) * 10)
    extra = bytes(round(a * 16) for a in (currents[20:24] + [0.0] * 4)[:4])
    return bytes([0, 0, 0, 33, 1]) + bits.to_bytes(27, "little") + extra + b"\x00"


def record(*, trip_ms: float = 5.0, loss_pct: float = 0.0, volts: float = 12.5, cpu_pct: float = 40.0,
           mask: int | None = None, can_pct: float = 30.0, currents: list[float] | None = None) -> bytes:
    base = struct.pack(">BbHBBBBH", round(trip_ms * 2), round(loss_pct / 4), round(volts * 256), round(cpu_pct * 2),
                       status() if mask is None else mask, round(can_pct * 2), 60, round(2.5 * 256))
    return base + (rev_pd(currents) if currents is not None else bytes([0, 0, 0, 0]))


def event(offset_s: float, text: str, start_unix_s: float = START_UNIX_S) -> bytes:
    t = start_unix_s + offset_s
    whole = int(t)
    raw = text.encode()
    return struct.pack(">qQi", whole + LABVIEW_TO_UNIX_S, int((t - whole) * 2**64), len(raw)) + raw


def build_session(folder: Path, name: str = "2024_03_16 14_13_45 Sat") -> Path:
    """A 10 s session (500 records): disabled 0-2 s, auto 2-4 s, disabled 4-5 s, teleop 5-9 s, disabled after.

    Battery 12.5 V, sagging to 6.5 V with a brownout at 7.0-7.2 s; channel 3 draws 40 A at the same time.
    Packet loss 20% at 6-6.5 s. Events: an FMS line at 0.5 s, an error at 7.1 s, a tagged print at 8 s.
    Returns the `.dslog` path.
    """
    records = []
    for i in range(500):
        t = i * 0.02
        mask = status(disabled=not (2 <= t < 4 or 5 <= t < 9), teleop=5 <= t < 9, brownout=7.0 <= t < 7.2)
        sag = 7.0 <= t < 7.2
        currents = [0.0] * 24
        currents[3] = 40.0 if sag else 2.0
        records.append(record(volts=6.5 if sag else 12.5, loss_pct=20.0 if 6 <= t < 6.5 else 0.0, mask=mask,
                              currents=currents))
    dslog = folder / f"{name}.dslog"
    dslog.write_bytes(header() + b"".join(records))
    events = [event(0.5, "<TagVersion>1 <time> 00.500 <message> FMS Connected: Qualification - 12:1"),
              event(7.1, "<TagVersion>1 <time> 07.100 <count> 1 <flags> 1 <Code> 44004 <details> "
                         "ERROR: Brownout detected <location> Driver Station <stack> "),
              event(8.0, "<TagVersion>1 <message> Shooter ready")]
    (folder / f"{name}.dsevents").write_bytes(header() + b"".join(events))
    return dslog

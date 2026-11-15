"""Running caniv: listing, inspecting and changing CANivore devices.

Its command line (seen from `caniv --help` on 1.1.1.0): `-d <serial or name>` or `-a` picks the device; actions are `--list`, `--info`, `--license`, `--blink`,
`--reset`, `--name <name>`, `--flash <file.crf>`, `--termination <arg>` and `--esp32 <arg>`. Seen with NO CANivore connected: `--list` prints only its header
row (`Serial Num  Name  Status  Application Vers`) and exits 0; any action on a device prints `Error value:-102` and `Error Message:"Could not open device"`
on stdout and exits 127; a bad file for `--flash` is caught first (`-206`, `CRF could not be read`); a missing argument is `-2`, `Invalid input`.

Then seen with one real CANivore (serial number 32 hex digits, name `canfd`): the `--list` row is the header's columns, and the header is as wide as the longest
serial (the Name column started at 34, not 20); `--info` prints tab-indented `Key:   value` lines (`USB Serial Number`, `USB Firmware Vers`, `USB Status`, `USB Name`,
`Application Firmware`, `Bootloader Firmware`, `Manufacture Date`, `ESP32 Enable`, `CAN Bus Termination`, supply voltages, `CAN Bus Util (%)`, `CAN Bus Enable`, `Reset Flags`)
and `--license` prints `Number of Licenses` and `License 0: ...`; `--blink` blinks the LED and prints nothing (exit 0). The list's `Application Vers` (26.6.0) equals the
info's `USB Firmware Vers`, NOT the `Application Firmware` (26.70.0.0) that the `.crf` firmware files carry. Not seen: the output of `--name`, `--reset` and `--flash` on
a real device (nothing was changed). So the list is read by the header's column positions (and, for writes, falls back to looking for the device as a word in the raw
text), and the answers of the actions are passed on as caniv's own lines. `--termination` and `--esp32` are NOT offered: caniv checks their value only after opening a device, so what it accepts
("on", "true", "1"?) could not be learned, and a wrong guess could switch the bus termination off.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.resolve import newest_installed_async
from frc_mcp_ctre.runner import RunResult, run_tool
from frc_mcp_ctre.store import Installed

from . import config

_ERROR = re.compile(r'Error value:\s*(-?\d+)\s*Error Message:\s*"([^"]*)"', re.IGNORECASE | re.DOTALL)
_DEVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,31}$")  # a serial number or a name; starts with a letter or digit (never looks like an option); no spaces, no `*` ("any device")
_HEADERS = ("Serial Num", "Name", "Status", "Application Vers")
_KEYS = ("serial", "name", "status", "app")
MAX_LINES = 30
# `--info` keys left out unless asked for: they are known from the list or change by the second, so they cost tokens and rarely matter. Anything else, including a key
# not seen on the test device, is kept.
QUIET_KEYS = {"USB Serial Number", "USB Name", "USB Status", "USB Discovery Status", "Manufacture Date", "Manufacture Revision", "V+ Supply (V)", "5V Supply (V)", "Reset Flags"}


def check_device(device: str) -> str:
    """A device name or serial number in the shape caniv takes; `*` (any device) is refused, so a change can never hit "whichever one answers"."""
    if device.strip() == "*":
        raise CtreError("Name the CANivore by serial number or name (caniv_list shows them); `*` (any device) is not allowed for a change.")
    if not _DEVICE.fullmatch(device):
        raise CtreError(f"{device!r} is not a CANivore serial number or name: use 1 to 32 letters, digits, `_`, `-` or `.`, starting with a letter or digit.")
    return device


def check_new_name(name: str) -> str:
    if not _DEVICE.fullmatch(name):
        raise CtreError(f"{name!r} is not allowed as a CANivore name: use 1 to 32 letters, digits, `_`, `-` or `.`, starting with a letter or digit.")
    return name


def lines(text: str) -> list[str]:
    """caniv's lines with the tabs and padding squeezed out (`Key:   value` becomes `Key: value`), blank lines dropped, bounded."""
    squeezed = (re.sub(r"\s{2,}", " ", line.replace("\t", " ")).strip() for line in text.splitlines())
    return [line[:200] for line in squeezed if line][:MAX_LINES]


def fields(text: str) -> dict[str, str]:
    """`Key: value` lines as a dictionary (caniv's own keys, unchanged apart from whitespace); lines without a colon are left out."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key.strip():
            found[key.strip()] = value.strip()[:100]
    return found


def failure(result: RunResult, what: str) -> CtreError | None:
    """caniv's own error (`Error value:-102` / `Error Message:"..."`, exit 127 when seen) as a CtreError saying what to do; None when the run was fine."""
    match = _ERROR.search(result.output)
    if match is None:
        if result.returncode == 0:
            return None
        return CtreError(f"caniv {what} failed (exit {result.returncode}): {result.output[:200] or 'no message'}")
    code, message = match.group(1), match.group(2)
    hints = {"-102": " No CANivore could be opened: is one connected by USB? (caniv_list shows what caniv can see.)",
             "-121": " The USB connection could not be opened: another program may be using the CANivore at this moment (seen when two caniv runs overlapped). Try again."}
    hint = hints.get(code, "")
    return CtreError(f"caniv {what} failed: {message} (error {code}).{hint}")


_usb = asyncio.Lock()  # one caniv user at a time: see `session`


class Session:
    """One tool call's use of caniv: the installed tool resolved once, and the USB held for the whole call (`session()` makes one).

    A CANivore cannot be opened by two caniv processes at once (seen with a real device: two overlapping runs, the second printed `Error value:-121`, `Could
    not open USB connection`), and an assistant may issue several tool calls together. So a call that needs several runs (look the device up in the list, then act on
    it) keeps the lock from the first to the last, and no other call can slip a change in between.
    """

    def __init__(self, exe: Installed) -> None:
        self.exe = exe

    async def run(self, args: list[str], what: str, timeout_s: float | None = None) -> RunResult:
        """Run caniv. Raises CtreError for a timeout or caniv's own error."""
        result = await run_tool(self.exe.path, args, timeout_s or config.timeout_s())
        if (problem := failure(result, what)) is not None:
            raise problem
        return result

    async def list_devices(self) -> tuple[list[dict[str, str]], str]:
        result = await self.run(["--list"], "list")
        return parse_list(result.output), result.output

    async def require_listed(self, device: str) -> str:
        """Refuse a change aimed at a device caniv does not list (a typo must not reach another device, or `any`); returns the identifier to pass to caniv."""
        wanted = check_device(device)  # a name that could mean "any device" never reaches caniv, not even for the list
        rows, text = await self.list_devices()
        if (found := listed(text, rows, wanted)) is None:
            seen = ", ".join(f"{r['serial']} ({r['name']})" if r["name"] else r["serial"] for r in rows) or "none"
            raise CtreError(f"caniv does not list a CANivore {device!r}. It lists: {seen}.")
        return found

    async def info(self, device: str | None, full: bool = False) -> dict[str, Any]:
        """`--info` and `--license` of one CANivore (or any, when none is named): caniv's own `Key: value` pairs and license lines, plus `firmware` (its `Application Firmware`).

        The pairs in `QUIET_KEYS` are left out unless `full`. A named device is looked up in the list first, so a name in another letter case reaches caniv as the serial it
        printed (as for every change).
        """
        pick = ["-d", await self.require_listed(device)] if device else ["--any"]
        information = await self.run([*pick, "--info"], "info")
        license_ = await self.run([*pick, "--license"], "license")
        pairs = fields(information.output)
        out: dict[str, Any] = {"device": device or "any"}
        if firmware := pairs.get("Application Firmware"):
            out["firmware"] = firmware  # the version `.crf` files carry; not the list's `Application Vers` column (see the module docstring)
        out["info"] = pairs if full else {k: v for k, v in pairs.items() if k not in QUIET_KEYS}
        out["license"] = lines(license_.output)
        return out


@asynccontextmanager
async def session() -> AsyncIterator[Session]:
    """`async with caniv.session() as cv:` resolves the newest installed caniv (a missing one, or macOS, raises CtreError with the reason) and then holds the USB."""
    exe = await newest_installed_async("caniv")
    async with _usb:
        yield Session(exe)


def parse_list(text: str) -> list[dict[str, str]]:
    """The rows of `caniv --list`, cut at the columns of its header line. Empty when there is no header or no row."""
    all_lines = text.splitlines()
    header = next((line for line in all_lines if line.lstrip().startswith(_HEADERS[0])), None)
    if header is None:
        return []
    starts = [header.find(h) for h in _HEADERS]
    if -1 in starts or starts != sorted(starts):
        return []
    rows = []
    for line in all_lines[all_lines.index(header) + 1:]:
        if not line.strip():
            continue
        ends = [*starts[1:], len(line) + 1]
        rows.append({key: line[start:end].strip() for key, start, end in zip(_KEYS, starts, ends, strict=True)})
    return rows


def listed(text: str, rows: list[dict[str, str]], device: str) -> str | None:
    """The identifier caniv itself printed for `device` (its serial number when it matched a parsed row by serial or name, in any letter case), or the name as
    given when the list could not be parsed and the device is the first word of a line of the raw text (not an error line); None when it is not listed."""
    wanted = device.lower()
    for row in rows:
        if wanted in (row["serial"].lower(), row["name"].lower()):
            return row["serial"]
    if not rows and any(line.split()[0].lower() == wanted for line in text.splitlines() if line.strip()
                        and not line.lstrip().lower().startswith(("error", "usage", "unknown", "invalid", "failed"))):
        return device
    return None

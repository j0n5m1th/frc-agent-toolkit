"""Running Owlet: choosing which installed version opens a hoot file, then info, signal scans and conversions.
Command line (seen from `owlet --help` on 26.2.0 and 26.70.0): `owlet <hoot> <out> [-f wpilog|mcap] [-s ID,ID] [--unlicensed]`,
`owlet <hoot> --scan [-F]` (one `name:  hexid` line per signal), `--compliancy` (prints `owlet: N` and `hoot log: M`), `--check-pro`.
A log needs the Owlet of exactly its compliancy (`pick`). Seen on a real log: a bad `-s` id prints `Invalid signal` and then
exports EVERYTHING (over 1 GB, split into `name.2.wpilog` files), and an existing output file is overwritten. So this module
checks every signal id itself and never exports without an explicit choice.
"""

import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from frc_mcp_ctre import choose
from frc_mcp_ctre.choose import compliancy_of
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.fetch import load_index
from frc_mcp_ctre.platform import platform_key
from frc_mcp_ctre.resolve import refused_message
from frc_mcp_ctre.runner import run_tool
from frc_mcp_ctre.store import Installed
from frc_mcp_ctre.store import scan as scan_installed

from . import config

MAX_ID_CHARS = 20000  # the signal list goes on one command line; Windows allows about 32000 characters in all
_COMPLIANCY = re.compile(r"owlet:\s*(\d+)\D+hoot log:\s*(\d+)", re.IGNORECASE | re.DOTALL)
SUFFIX = ".wpilog"  # Owlet can also write .mcap; this server does not offer it
_SPLIT = r"^{stem}\.(\d+)\.wpilog$"


@dataclass(frozen=True)
class Picked:
    exe: Installed
    compliancy: int  # the log's, and so this Owlet's: they must be equal


@dataclass(frozen=True)
class Signal:
    name: str
    id: str


HEADER_OFFSET = 70  # seen: the byte at offset 70 of a .hoot file is its compliancy (same as `owlet --compliancy`'s "hoot log", on levels 19 and 21); AdvantageScope reads it the same way
OLDEST_COMPLIANCY = 6  # Phoenix 2024; AdvantageScope also refuses anything older


def log_compliancy(hoot: Path) -> int:
    """The log's compliancy, read from the file header (no Owlet needed, so it works before any Owlet is installed)."""
    try:
        with hoot.open("rb") as handle:
            header = handle.read(HEADER_OFFSET + 1)
    except OSError as e:
        raise CtreError(f"{hoot.name} could not be read: {e}") from e
    if len(header) <= HEADER_OFFSET:
        raise CtreError(f"{hoot.name} is too short to be a hoot file.")
    level = header[HEADER_OFFSET]
    if level < OLDEST_COMPLIANCY:
        raise CtreError(f"{hoot.name} reports compliancy {level}, older than Owlet can read (Phoenix 2024 or newer is needed), or it is not a hoot file.")
    return level


def header_level(hoot: Path) -> int | None:
    """`log_compliancy`, or None for a file that does not look like a hoot file (for listings, which must not fail on one bad file)."""
    try:
        return log_compliancy(hoot)
    except CtreError:
        return None


async def _ask(exe: Path, hoot: Path) -> tuple[int, int] | None:
    """(this Owlet's compliancy, the log's compliancy) as Owlet itself reports them; None when it would not say."""
    result = await run_tool(exe, [str(hoot), "--compliancy"], 60)
    match = _COMPLIANCY.search(result.output)
    return (int(match[1]), int(match[2])) if match else None


async def pick(hoot: Path) -> Picked:
    """The installed Owlet whose compliancy equals the log's (the newest build of it). No other level reads the log correctly."""
    level = log_compliancy(hoot)
    index = (await asyncio.to_thread(load_index, offline=True)).index
    found = (await asyncio.to_thread(scan_installed, ["owlet"], index))["owlet"]
    have = [i for i in found if i.verified]  # a copy that is not verified is never run
    refused = [i for i in found if not i.verified]
    exact: list[Installed] = []
    for item in have:
        known = compliancy_of(index, item)
        if known is None and (answer := await _ask(item.path, hoot)):  # not in the index or the file name: ask the program itself
            known = answer[0]
        if known == level:
            exact.append(item)
    if exact:
        return Picked(exact[0], level)  # `have` is newest first
    wanted = choose.for_compliancy(index, "owlet", level, platform_key())
    if wanted is None:
        raise CtreError(f"{hoot.name} is a compliancy {level} log and CTRE's list has no Owlet for that level on this computer. Refresh the list with ctre_status refresh=true; if it is still missing, the log's Phoenix version is newer than CTRE's tools.")
    levels = sorted({c for i in have if (c := compliancy_of(index, i)) is not None})
    installed_text = f"Installed Owlets are for compliancy {', '.join(str(c) for c in levels)}." if levels else "No Owlet is installed."
    if refused:
        installed_text += " " + refused_message("owlet", refused)
    raise CtreError(f"{hoot.name} is a compliancy {level} log. Only an Owlet of exactly that level reads it correctly (others refuse it or return almost nothing). {installed_text} Install it with ctre_install tool=owlet compliancy={level} (ctre-tools server, needs CTRE_ALLOW_INSTALL=1): that is Owlet {wanted.version}.")


async def info(hoot: Path, picked: Picked) -> dict[str, object]:
    check = await run_tool(picked.exe.path, [str(hoot), "--check-pro"], 120)
    text = check.output.strip()
    if check.returncode != 0:
        raise CtreError(f"Owlet {picked.exe.version} could not check {hoot.name}: {text[:200]}")
    return {"file": hoot.name, "mb": round(hoot.stat().st_size / 2**20, 1), "compliancy": picked.compliancy,
            "owlet": f"{picked.exe.version} ({picked.exe.source})", "pro": "NOT" not in text}  # same test AdvantageScope uses


def parse_scan(text: str) -> list[Signal]:
    """`name:   hexid` lines to signals.
    Owlet pads every name to one common width (seen: id at column 74 in one file, 62 in another), so only lines whose id starts
    at the most common column count. A lone line whose name has a space is a message (`Input file is an unsupported version: 2`).
    """
    candidates = []
    for line in text.splitlines():
        line = line.rstrip()
        parts = line.rsplit(None, 1)
        if len(parts) == 2 and parts[0].endswith(":") and re.fullmatch(r"[0-9a-fA-F]+", parts[1]):
            candidates.append((len(line) - len(parts[1]), parts[0][:-1].rstrip(), parts[1].lower()))
    if not candidates:
        return []
    column = max(Counter(c[0] for c in candidates).items(), key=lambda kv: kv[1])[0]
    found = [Signal(name, ident) for col, name, ident in candidates if col == column]
    return [] if len(found) == 1 and " " in found[0].name else found


async def scan(hoot: Path, picked: Picked) -> list[Signal]:
    result = await run_tool(picked.exe.path, [str(hoot), "--scan"], config.timeout_s())
    if result.returncode != 0:  # seen: a log of another level exits 1 and says so (`Input file is an unsupported version: 1`)
        raise CtreError(f"Owlet {picked.exe.version} could not scan {hoot.name} (exit {result.returncode}): {result.output[:300] or 'no message'}")
    signals = parse_scan(result.output)
    if not signals:
        raise CtreError(f"Owlet found no signals in {hoot.name}: {result.output[:300] or 'it printed nothing'}")
    return signals


def group_of(name: str) -> str:
    """A signal's device group: its path without the last part, at most three parts (`Phoenix6/<bus>/<device>`)."""
    parts = name.split("/")
    return "/".join(parts[:-1][:3]) if len(parts) > 1 else name


def groups(signals: list[Signal], top: int = 8) -> tuple[dict[str, int], int]:
    """The `top` biggest device groups with their signal counts, and how many smaller groups were left out."""
    counts: dict[str, int] = {}
    for signal in signals:
        counts[group_of(signal.name)] = counts.get(group_of(signal.name), 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    return dict(ranked[:top]), max(0, len(ranked) - top)


def select_signals(signals: list[Signal], wanted: list[str] | None, name_contains: str | None) -> list[Signal]:
    """The signals named by id or exact name in `wanted`, plus those whose name contains `name_contains`. Anything unknown is an error (Owlet would export everything)."""
    by_id = {s.id: s for s in signals}
    by_name = {s.name: s for s in signals}
    chosen: dict[str, Signal] = {}
    unknown = []
    for token in wanted or []:
        hit = by_id.get(token.lower().removeprefix("0x")) or by_name.get(token)
        if hit:
            chosen[hit.id] = hit
        else:
            unknown.append(token)
    if unknown:
        raise CtreError(f"Not signals of this file: {', '.join(repr(u) for u in unknown[:5])}. Use owlet_signals to find ids and exact names.")
    if name_contains:
        needle = name_contains.lower()
        matches = [s for s in signals if needle in s.name.lower()]
        if not matches:
            raise CtreError(f"No signal name contains {name_contains!r}. Use owlet_signals to look.")
        chosen.update({s.id: s for s in matches})
    return list(chosen.values())


def split_parts(target: Path) -> list[Path]:
    """The extra files Owlet starts at 1 GB: `name.2.wpilog`, `name.3.wpilog`..."""
    return sorted(p for p in target.parent.iterdir() if re.match(_SPLIT.format(stem=re.escape(target.stem)), p.name))


def _remove_outputs(target: Path, before: set[Path]) -> None:
    """Delete what a failed conversion of THIS run wrote (the target did not exist before: `new_output_path` refuses an existing one; split parts in `before` were there already)."""
    for path in [target, *(p for p in split_parts(target) if p not in before)]:
        path.unlink(missing_ok=True)


async def convert(hoot: Path, picked: Picked, target: Path, chosen: list[Signal] | None, unlicensed: bool) -> dict[str, object]:
    """Run the conversion (`chosen` None = every signal) and describe the files made."""
    args = [str(hoot), str(target), "-f", "wpilog"]
    if chosen is not None:
        if not chosen:  # Owlet given an empty `-s` list could export everything
            raise CtreError("No signals were chosen. Name signals, or use all_signals=true.")
        ids = ",".join(s.id for s in chosen)
        if len(ids) > MAX_ID_CHARS:
            raise CtreError(f"{len(chosen)} signals is too many for one command; narrow the choice (or use all_signals=true).")
        args += ["-s", ids]
    if unlicensed:
        args.append("--unlicensed")
    before = set(split_parts(target))  # parts left by an earlier export with this name: never removed or reported as this run's
    try:
        result = await run_tool(picked.exe.path, args, config.timeout_s())
    except CtreError:  # timed out or could not start: do not leave a half-written file behind
        _remove_outputs(target, before)
        raise
    if result.returncode != 0 or not target.is_file():
        _remove_outputs(target, before)
        hint = " If it mentions a license, try unlicensed=true (Pro signals are then left out)." if "licen" in result.output.lower() else ""
        raise CtreError(f"Owlet failed (exit {result.returncode}): {result.output[-300:] or 'no message'}.{hint}")
    parts = [p for p in split_parts(target) if p not in before]
    files = [target, *parts]
    out: dict[str, object] = {"file": target.name, "signals": len(chosen) if chosen is not None else "all",
                              "mb": round(sum(f.stat().st_size for f in files) / 2**20, 1)}  # beside the hoot file; the Owlet used is in owlet_info
    if parts:
        out["also"] = [p.name for p in parts]  # Owlet starts a new file at 1 GB
    skipped = [line for line in result.output.splitlines() if "invalid" in line.lower() or "warn" in line.lower()]
    if skipped:
        out["owlet_says"] = skipped[:3]
    return out

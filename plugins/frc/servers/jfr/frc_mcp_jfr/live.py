"""Answers about a JVM that is running now (no recording): which classes are growing, and what its threads are doing.

Pure functions over the text `jcmd <pid> GC.class_histogram` and `jcmd <pid> Thread.print` print, so they are tested without a JDK.
"""

import re
from typing import Any

from .analysis import java_class

_HISTOGRAM_ROW = re.compile(r"^\s*\d+:\s+(\d+)\s+(\d+)\s+(\S.*?)(?:\s+\([^)]*\))?\s*$")
_THREAD_HEAD = re.compile(r'^"(?P<name>.+?)"')
_STATE = re.compile(r"java\.lang\.Thread\.State:\s*(?P<state>[A-Z_]+)")
_FRAME = re.compile(r"^\s+at\s+(?P<method>[^\s(]+)")
_LOCK = re.compile(r"^\s+- (?P<verb>waiting to lock|locked|parking to wait for|waiting on)\s+<(?P<addr>[^>]+)>(?:\s+\(a (?P<kind>[^)]+)\))?")
_INTERNAL_FRAMES = ("java.lang.Thread.run", "java.lang.Thread.runWith")
FRAMES_SHOWN = 4


def parse_histogram(text: str) -> dict[str, tuple[int, int]]:
    """class (Java name) -> (instances, bytes) from `GC.class_histogram` output."""
    found: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        if match := _HISTOGRAM_ROW.match(line):
            found[java_class(match.group(3))] = (int(match.group(1)), int(match.group(2)))
    return found


def histogram_growth(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]], top: int) -> dict[str, Any]:
    """The classes whose live bytes grew most between two histograms, and the totals."""
    rows: list[dict[str, Any]] = []
    for name, (count, size) in after.items():
        old_count, old_size = before.get(name, (0, 0))
        if size > old_size:
            rows.append({"class": name, "instances": f"+{count - old_count}", "kb": round((size - old_size) / 1024), "of": count})
    rows.sort(key=lambda r: r["kb"], reverse=True)
    total_before = sum(size for _, size in before.values())
    total_after = sum(size for _, size in after.values())
    out: dict[str, Any] = {"live_mb_before": round(total_before / 2**20, 2), "live_mb_after": round(total_after / 2**20, 2),
                           "growth_kb": round((total_after - total_before) / 1024), "rows": rows[:top]}
    if len(rows) > top:
        out["omitted_classes"] = len(rows) - top
    return out


def parse_threads(text: str) -> list[dict[str, Any]]:
    """One dict per Java thread with frames: name, state, top frames, and the lock it waits for or holds."""
    threads: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        head = next((_THREAD_HEAD.match(line) for line in lines if line.startswith('"')), None)
        if not head:
            continue
        state = next((m.group("state") for line in lines if (m := _STATE.search(line))), None)
        frames = [m.group("method") for line in lines if (m := _FRAME.match(line))]
        locks = [m.groupdict() for line in lines if (m := _LOCK.match(line))]
        if not state or not frames:
            continue  # JVM-internal threads (GC, compiler) have no Java frames
        thread: dict[str, Any] = {"name": head.group("name"), "state": state, "frames": frames}
        thread["waits_for"] = next(((lock["kind"] or "?", lock["addr"]) for lock in locks if lock["verb"] in ("waiting to lock", "parking to wait for")), None)
        thread["holds"] = [lock["addr"] for lock in locks if lock["verb"] == "locked"]
        threads.append(thread)
    return threads


def deadlock_text(text: str) -> str | None:
    """The JVM's own report of a Java-level deadlock (the cycle), trimmed, or None."""
    start = text.find("Found one Java-level deadlock")
    if start < 0:
        return None
    end = text.find("Found ", text.find("deadlock", start + 30) + 1)
    section = text[start: end if end > start else None]
    return "\n".join(line.strip() for line in section.splitlines() if line.strip())[:1500]


def thread_report(text: str, top: int) -> dict[str, Any]:
    """Counts by state, the threads blocked on a lock (with who holds it), then the busiest-looking others; plus any deadlock."""
    threads = parse_threads(text)
    holders = {addr: t["name"] for t in threads for addr in t["holds"]}
    states: dict[str, int] = {}
    for t in threads:
        states[t["state"]] = states.get(t["state"], 0) + 1
    order = {"BLOCKED": 0, "RUNNABLE": 1}
    ranked = sorted(threads, key=lambda t: order.get(t["state"], 2))
    rows = []
    for t in ranked[:top]:
        row: dict[str, Any] = {"name": t["name"], "state": t["state"], "frames": [f for f in t["frames"] if f not in _INTERNAL_FRAMES][:FRAMES_SHOWN]}
        if t["waits_for"]:
            kind, addr = t["waits_for"]
            row["waits_for"] = kind
            if addr in holders and holders[addr] != t["name"]:
                row["held_by"] = holders[addr]
        rows.append(row)
    out: dict[str, Any] = {"threads": len(threads), "states": states, "rows": rows}
    if len(ranked) > top:
        out["omitted_threads"] = len(ranked) - top
    out["deadlock"] = deadlock_text(text) or False
    return out

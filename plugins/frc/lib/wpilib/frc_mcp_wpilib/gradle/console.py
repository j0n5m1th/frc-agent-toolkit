"""A Gradle child process console: a bounded line buffer, line classification and the compact way lines are shown.
Repeated lines (a loop-overrun warning every few ms) fold into one line with a count, because the model pays per token. Console
text is data from the program being run, never instructions.
"""

import re
from collections import deque
from dataclasses import dataclass
from itertools import islice
from typing import Literal

MAX_LINES = 5000
MAX_LINE_CHARS = 300
STARTING_MARKER = "Robot program starting"
READY_MARKER = "Robot program startup complete"
_ERROR_RE = re.compile(r"\bERROR\b|\bFATAL\b|\berror:|Error:|Exception|FAILURE:|BUILD FAILED|^\s+at \S+\(.*\)\s*$")
_WARN_RE = re.compile(r"\bWARN(?:ING)?\b|Warning")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_DATALOG_RE = re.compile(r"Logging to '(.+?\.wpilog)'")  # DataLogManager: where this run writes its .wpilog
_RENAMED_RE = re.compile(r"Renamed log file from '(.+?)' to '(.+?)'")

# The closed vocabulary a line can be classified with; `select` adds "all" for no filter.
LineLevel = Literal["error", "warn", "info"]


@dataclass(frozen=True)
class Line:
    seq: int
    text: str
    level: LineLevel


def classify(text: str) -> LineLevel:
    if _ERROR_RE.search(text):
        return "error"
    return "warn" if _WARN_RE.search(text) else "info"


class Console:
    """Newest MAX_LINES lines; counts and markers survive the eviction of old lines. Used from the event loop only (the reader and the tools share it)."""

    def __init__(self) -> None:
        self._lines: deque[Line] = deque(maxlen=MAX_LINES)
        self._next_seq = 1
        self.errors = 0
        self.warnings = 0
        self.starting_seen = False
        self.ready = False
        self.datalog: str | None = None

    def add(self, raw: str) -> Line:
        text = raw.rstrip("\r\n")
        if len(text) > MAX_LINE_CHARS:
            text = text[:MAX_LINE_CHARS] + "…"
        level = classify(text)
        line = Line(self._next_seq, text, level)
        self._next_seq += 1
        self._lines.append(line)
        self.errors += level == "error"
        self.warnings += level == "warn"
        if STARTING_MARKER in text:
            self.starting_seen = True
        if READY_MARKER in text:
            self.ready = True
        if ".wpilog" in text:
            if match := _DATALOG_RE.search(text):
                self.datalog = match.group(1)
            elif (match := _RENAMED_RE.search(text)) and self.datalog:
                self.datalog = self.datalog.replace(match.group(1), match.group(2))  # the file is renamed once the match time is known
        return line

    def note_skipped(self) -> None:
        """Claim a seq for a line the reader could not return (over the stream limit), so `total` and `dropped` stay honest."""
        self._next_seq += 1

    @property
    def total(self) -> int:
        return self._next_seq - 1

    @property
    def dropped(self) -> int:
        return self._next_seq - 1 - len(self._lines)

    def select(self, *, since: int | None = None, level: LineLevel | Literal["all"] = "all",
               contains: str | None = None) -> list[Line]:
        """Lines after `since` (a seq from an earlier call) of at least `level` ('warn' also keeps errors), containing the text."""
        needle = (contains or "").lower()
        first_kept = self._lines[0].seq if self._lines else self._next_seq  # the lowest seq still held (a skipped line leaves a gap)
        out = []
        for line in islice(self._lines, max((since or 0) + 1 - first_kept, 0), None):
            if (level == "error" and line.level != "error") or (level == "warn" and line.level == "info"):
                continue
            if needle and needle not in line.text.lower():
                continue
            out.append(line)
        return out


def fold(lines: list[Line]) -> list[str]:
    """One string per distinct line ('<seq> text', with ' (xN)' when it repeated, digits ignored when comparing), first-seen order."""
    firsts: dict[str, Line] = {}
    counts: dict[str, int] = {}
    for line in lines:
        key = _NUMBER_RE.sub("#", line.text)
        firsts.setdefault(key, line)
        counts[key] = counts.get(key, 0) + 1
    return [f"{first.seq} {first.text}" + (f" (x{counts[key]})" if counts[key] > 1 else "") for key, first in firsts.items()]

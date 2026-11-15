"""Talking to the JDK's own tools: `jcmd` (running JVMs, start a recording) and `jfr` (read a recording). No shell is involved."""

import asyncio
import contextlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .errors import JfrError

_READ_CHUNK = 1 << 16


async def _read_capped(stream: asyncio.StreamReader, limit: int, tool: str) -> bytes:
    """Read `stream` to the end, refusing as soon as it passes `limit` bytes."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await stream.read(_READ_CHUNK):
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            raise JfrError(f"`{tool}` printed more than {limit // 2**20} MB. Ask about one event type or a shorter recording, or raise JFR_MAX_OUTPUT_MB.")
    return b"".join(chunks)


async def _stop(proc: asyncio.subprocess.Process) -> None:
    """Kill a run that is still alive and wait for its exit; a finished one is left as it is."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


def _first_line(*texts: bytes) -> str:
    """The first non-empty line of the first text that has one."""
    for text in texts:
        if line := text.decode("utf-8", errors="replace").strip():
            return line.splitlines()[0]
    return "no message"


async def run(tool: str, args: list[str], *, tool_path: str | None = None) -> str:
    """Run a JDK tool (`tool_path` if given, else the configured JDK's) and return its text output.

    Raises JfrError if it fails, takes too long or prints more than JFR_MAX_OUTPUT_MB (the tool is stopped at that point)."""
    limit, deadline = config.max_output_bytes(), config.timeout_s()
    proc = await asyncio.create_subprocess_exec(
        tool_path or config.jdk_tool(tool), *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    if proc.stdout is None or proc.stderr is None:  # both are PIPEd above; explicit so `python -O` keeps the message
        raise JfrError(f"`{tool}` did not give its stdout and stderr pipes.")
    stderr_read = asyncio.create_task(proc.stderr.read())  # drained alongside stdout, so a chatty tool cannot fill the pipe and stall
    try:
        async with asyncio.timeout(deadline):
            out = await _read_capped(proc.stdout, limit, tool)
            code = await proc.wait()
            err = await stderr_read
    except TimeoutError:
        raise JfrError(f"`{tool}` took longer than {deadline:g} s and was stopped (JFR_TIMEOUT_S).") from None
    finally:
        stderr_read.cancel()  # a no-op once the task has its bytes
        await _stop(proc)
    if code != 0:
        raise JfrError(f"`{tool}` failed: {_first_line(err, out)}.")
    return out.decode("utf-8", errors="replace")


# Events whose values are secrets (every environment variable and system property of the program, often tokens). Never recorded
# (JFR.start gets `event#enabled=false`, a JDK 17+ option; older jcmd refuses it) and never read back from recordings made elsewhere.
# jdk.SystemProcess holds the command line of every process on the computer (tokens included) and is on in the default and profile settings.
SECRET_EVENTS = ("jdk.InitialEnvironmentVariable", "jdk.InitialSystemProperty", "jdk.SystemProcess")
NO_SECRET_EVENTS = [f"{name}#enabled=false" for name in SECRET_EVENTS]

# What may be read is an allow-list, so an event a newer JDK adds (agent options, process start commands, security property values, JVM flags)
# is refused until someone looks at it. These `jdk.` events hold timings, counts, names of code and sizes. Events a program defines itself
# (any name outside `jdk.`) are the team's own and are read.
SAFE_EVENTS = frozenset({
    "jdk.ExecutionSample", "jdk.NativeMethodSample", "jdk.MethodTrace", "jdk.MethodTiming",
    "jdk.JavaMonitorEnter", "jdk.JavaMonitorWait", "jdk.ThreadPark", "jdk.ThreadSleep", "jdk.ThreadStart", "jdk.ThreadEnd",
    "jdk.JavaThreadStatistics", "jdk.ThreadCPULoad", "jdk.CPULoad", "jdk.ThreadAllocationStatistics", "jdk.ThreadContextSwitchRate",
    "jdk.GarbageCollection", "jdk.YoungGarbageCollection", "jdk.OldGarbageCollection", "jdk.G1GarbageCollection",
    "jdk.GCPhasePause", "jdk.GCPhasePauseLevel1", "jdk.GCPhasePauseLevel2", "jdk.GCPhasePauseLevel3", "jdk.GCPhasePauseLevel4",
    "jdk.GCPhaseConcurrent", "jdk.GCHeapSummary", "jdk.G1HeapSummary", "jdk.GCReferenceStatistics", "jdk.GCCPUTime",
    "jdk.PromotionFailed", "jdk.ConcurrentModeFailure", "jdk.MetaspaceSummary", "jdk.MetaspaceGCThreshold", "jdk.PSHeapSummary",
    "jdk.ObjectAllocationSample", "jdk.ObjectAllocationInNewTLAB", "jdk.ObjectAllocationOutsideTLAB", "jdk.OldObjectSample",
    "jdk.ObjectCount", "jdk.ClassLoadingStatistics", "jdk.ClassLoad", "jdk.ClassDefine", "jdk.ClassUnload",
    "jdk.Compilation", "jdk.CompilerPhase", "jdk.CompilationFailure", "jdk.Deoptimization", "jdk.CodeCacheStatistics", "jdk.CompilerStatistics",
    "jdk.SafepointBegin", "jdk.SafepointEnd", "jdk.SafepointStateSynchronization", "jdk.SafepointWaitBlocked", "jdk.SafepointCleanup",
    "jdk.ExecuteVMOperation", "jdk.FileRead", "jdk.FileWrite", "jdk.FileForce", "jdk.SocketRead", "jdk.SocketWrite",
    "jdk.PhysicalMemory", "jdk.CPUInformation", "jdk.NetworkUtilization", "jdk.ExceptionStatistics", "jdk.JVMInformation",
})
# Fields that can carry arguments, options or commands, dropped from every `jdk.` event even on the allow-list (JVMInformation has two of them).
SECRET_FIELDS = frozenset({"jvmArguments", "javaArguments", "jvmFlags", "options", "command", "commandLine", "arguments", "environment"})


def refuse_secret_events(names: list[str]) -> None:
    """Raise unless every event type may be read: not a secret one, and (for a `jdk.` event) on the allow-list."""
    for name in names:
        if name in SECRET_EVENTS:
            raise JfrError(f"{name} holds the program's environment or properties (often secrets) and is never read.")
        if name.startswith("jdk.") and name not in SAFE_EVENTS:
            raise JfrError(f"{name} is not one of the event types this server reads (it may hold settings or arguments). "
                           "Try jdk.ExecutionSample, jdk.GarbageCollection or jdk.ObjectAllocationSample; jfr_summary lists what a recording has.")


def drop_secret_fields(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events without the fields that must never be returned."""
    for event in found:
        if str(event.get("type", "")).startswith("jdk."):
            for field in SECRET_FIELDS & event.keys():
                del event[field]
    return found


def redact_command(command: str) -> str:
    """A `jcmd -l` command line without the program's arguments (they can hold tokens): the main class or jar, and a following `.java` or `.jar` file name."""
    parts = command.split()
    kept = parts[:1]
    if len(parts) > 1 and re.search(r"\.(java|jar)$", parts[1], re.I) and parts[1][0] != "-":
        kept.append(parts[1])
    return " ".join(kept) + (" ..." if len(parts) > len(kept) else "")


@dataclass(frozen=True)
class Jvm:
    pid: int
    command: str


async def list_jvms(contains: str | None = None) -> list[Jvm]:
    """Running JVMs of this user (`jcmd -l`), without the jcmd process itself, optionally filtered by their command line."""
    found = []
    needle = (contains or "").lower()
    for line in (await run("jcmd", ["-l"])).splitlines():
        pid, _, command = line.strip().partition(" ")
        if not pid.isdigit() or "jdk.jcmd" in command or "JCmd" in command:
            continue
        shown = redact_command(command)  # filtered on what is shown, so a filter cannot probe the hidden arguments
        if needle in shown.lower():
            found.append(Jvm(int(pid), shown))
    return found


async def target_jcmd(pid: int) -> str | None:
    """The `jcmd` of the JDK the program itself runs on (its `java.home`), or None if it cannot be found.

    Talking to a JVM with its own JDK's tool always matches versions, whichever JDK this server was configured with."""
    try:
        text = await run("jcmd", [str(pid), "VM.system_properties"])
    except JfrError:
        return None
    match = re.search(r"^java\.home=(.*)$", text, re.M)
    if not match:
        return None
    home = re.sub(r"\\(.)", r"\1", match.group(1).strip())  # properties escape ':' and '\' on the way out
    return shutil.which("jcmd", path=str(Path(home) / "bin"))


async def jcmd_for(pid: int) -> str | None:
    """Check `pid` is a JVM this user can reach, and return the `jcmd` of its own JDK (None: use the configured one)."""
    jvms, own = await asyncio.gather(list_jvms(), target_jcmd(pid))
    if pid not in {j.pid for j in jvms}:
        raise JfrError(f"No running JVM has pid {pid}. jfr_list(kind='jvms') shows the ones this user can record.")
    return own


_START_RE = re.compile(r"^\s*Start:\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", re.M)
_DURATION_RE = re.compile(r"^\s*Duration:\s+([\d.]+)\s*(\w+)", re.M)
_EVENT_RE = re.compile(r"^\s*([\w.$]+)\s+(\d+)\s+(\d+)\s*$", re.M)
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


# What `jfr print --json` makes per event, measured on real recordings: about 0.6 KB for the event itself and 2.9 KB for each stack
# frame kept (every frame carries its full method and class description). The JSON is the size problem, not the file.
BYTES_PER_EVENT = 700
BYTES_PER_FRAME = 2900


def json_bytes(count: int, depth: int) -> int:
    return count * (BYTES_PER_EVENT + BYTES_PER_FRAME * depth)


def fit_depth(count: int, wanted: int, floor: int = 0) -> int:
    """The deepest stack (down to `floor`) whose JSON for `count` events stays within JFR_MAX_OUTPUT_MB; `floor` if none does."""
    depth = wanted
    while depth > floor and json_bytes(count, depth) > config.max_output_bytes():
        depth -= 1
    return depth


def check_output_size(summary: "Summary", names: list[str], depth: int) -> None:
    """Refuse a question whose JSON would be too big, before `jfr` is run: the counts come from the summary."""
    count, size, limit = summary.count(names), json_bytes(summary.count(names), depth), config.max_output_bytes()
    if size > limit:
        raise JfrError(f"{', '.join(names)}: {count:,} events would make about {size / 2**20:.0f} MB of JSON, over the "
                       f"{limit / 2**20:.0f} MB limit (JFR_MAX_OUTPUT_MB). Use a shorter recording, a rarer event type, or raise the limit.")


async def read_jfr(args: list[str]) -> str:
    """`jfr <args>` (reading a recording): a failure also says what to try when the recording came from a newer JDK."""
    try:
        return await run("jfr", args)
    except JfrError as e:
        raise JfrError(f"{e} If the recording came from a newer JDK, read it with that one: set JFR_JAVA_HOME.") from None


@dataclass(frozen=True)
class Summary:
    start: datetime | None  # UTC, to the second (all `jfr summary` prints)
    duration_s: float | None
    events: dict[str, int]

    def count(self, names: list[str]) -> int:
        """How many events of these types the recording holds."""
        return sum(self.events.get(name, 0) for name in names)

    @property
    def seconds(self) -> float | None:
        return round(self.duration_s, 1) if self.duration_s is not None else None


async def summary(path: Path) -> Summary:
    text = await read_jfr(["summary", str(path)])
    start = _START_RE.search(text)
    duration = _DURATION_RE.search(text)
    return Summary(
        datetime.fromisoformat(start.group(1).replace(" ", "T")) if start else None,
        float(duration.group(1)) * _UNITS.get(duration.group(2), 1.0) if duration else None,
        {m.group(1): int(m.group(2)) for m in _EVENT_RE.finditer(text) if "." in m.group(1)},
    )


def slim(node: dict[str, Any]) -> Any:
    """`json.loads` object hook: a thread, class or method description becomes just its name while the JSON is being read.

    `jfr print --json` describes every thread, class and method in full each time it appears (a stack frame is about 3 KB of
    JSON). Nothing here needs more than the name, and keeping only that makes the parsed events 2 to 10 times smaller in memory."""
    if "descriptor" in node and "type" in node and "name" in node:  # a method: Class.method
        owner = node["type"]
        return f"{owner['name'] if isinstance(owner, dict) else owner}.{node['name']}".replace("/", ".")
    if "osThreadId" in node or "javaThreadId" in node:  # a thread
        return node.get("javaName") or node.get("osName")
    if "name" in node and ("modifiers" in node or "classLoader" in node):  # a class
        return node["name"]
    return node


async def events(path: Path, names: list[str], depth: int = 0) -> list[dict[str, Any]]:
    """The recording's events of the given types as dicts (`type`, plus each field of `values`), via `jfr print --json`."""
    refuse_secret_events(names)
    text = await read_jfr(["print", "--json", "--events", ",".join(names), "--stack-depth", str(depth), str(path)])
    try:
        raw = await asyncio.to_thread(json.loads, text, object_hook=slim)
        return drop_secret_fields([{"type": e["type"], **e["values"]} for e in raw["recording"]["events"]])
    except (ValueError, KeyError, TypeError) as e:
        raise JfrError(f"Could not read the output of `jfr print`: {e}") from e

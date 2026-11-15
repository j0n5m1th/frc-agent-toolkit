"""The `frc-mcp` command: one program that runs any server, checks them all and starts the practice data.
    frc-mcp <server> [check|...]   run one server (what an MCP config calls); the rest of the line goes to that server
    frc-mcp doctor                 run every server's safe `check`
    frc-mcp demo robot|log         a practice robot, or a practice log
    frc-mcp hook ask "<reason>"    print the one JSON line the plugin's PreToolUse hook answers with (make Claude Code ask first)
Servers and demos are found through package entry points (groups `frc_mcp.servers` and `frc_mcp.demos`), so this module imports no
server; a server's spec is imported only when asked for or checked.
"""

import contextlib
import importlib.metadata
import io
import json
import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .spec import ServerSpec

SERVERS_GROUP = "frc_mcp.servers"
DEMOS_GROUP = "frc_mcp.demos"
RESERVED = ("doctor", "demo", "help", "hook")  # words that are commands, so no server may use them as its key
SHOWN_LINES = 8  # how much of a failed check doctor prints

HELP = """usage: frc-mcp <server> [check|...]      run one server (the rest of the line goes to that server)
       frc-mcp <server> --help            what that server accepts
       frc-mcp doctor                     run the safe checks of every server
       frc-mcp demo robot|log             a practice robot, or a practice log
       frc-mcp hook ask "<reason>"        what the plugin's PreToolUse hook prints (it asks the person to confirm the call)

servers: {servers}
demos:   {demos}

`frc-mcp doctor` only looks at this computer. It skips the checks that talk to a robot, a controller or a device;
run those yourself with `frc-mcp <server> check`."""


def _points(group: str) -> dict[str, importlib.metadata.EntryPoint]:
    return {ep.name: ep for ep in importlib.metadata.entry_points(group=group)}


def server_keys() -> list[str]:
    """The registered server keys, sorted. Reads metadata only: no spec is imported."""
    return sorted(_points(SERVERS_GROUP))


def demo_names() -> list[str]:
    return sorted(_points(DEMOS_GROUP))


def load_spec(key: str) -> "ServerSpec":
    """Import one server's spec. `KeyError` for a key nobody registered."""
    return _points(SERVERS_GROUP)[key].load()  # type: ignore[no-any-return]


def help_text() -> str:
    return HELP.format(servers=", ".join(server_keys()) or "(none installed)", demos=", ".join(demo_names()) or "(none installed)")


def _unknown(kind: str, word: str, choices: Sequence[str]) -> int:
    print(f"frc-mcp: unknown {kind} '{word}'. Choose one of: {', '.join(choices) or '(none installed)'}", file=sys.stderr)
    return 2


def _demo(args: Sequence[str]) -> int:
    names = demo_names()
    if not args or args[0] in ("-h", "--help"):
        print(f"usage: frc-mcp demo <name> [options]\ndemos: {', '.join(names) or '(none installed)'}\n(`frc-mcp demo <name> --help` shows the options of one demo.)")
        return 0 if args else 2
    if args[0] not in names:
        return _unknown("demo", args[0], names)
    run: Callable[[Sequence[str]], int | None] = _points(DEMOS_GROUP)[args[0]].load()
    return run(list(args[1:])) or 0


def _hook(args: Sequence[str]) -> int:
    """`hook ask "<reason>"`: print the PreToolUse answer that makes Claude Code ask the person first. Reads nothing, touches nothing."""
    if len(args) != 2 or args[0] != "ask" or not args[1].strip() or "\n" in args[1]:
        print('usage: frc-mcp hook ask "<one-line reason>"', file=sys.stderr)
        return 0 if args[:1] in (["-h"], ["--help"]) else 2
    answer = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": args[1].strip()}}
    print(json.dumps(answer, separators=(",", ":")))
    return 0


def _run_check(key: str) -> tuple[bool, str]:
    """Run one server's check with its own default settings. Returns (passed, what it printed). Nothing a check does stops the others."""
    out = io.StringIO()
    try:
        spec = load_spec(key)
        with contextlib.redirect_stdout(out):
            code = spec.run(["check"], prog=f"frc-mcp {key}")
    except SystemExit as e:  # a check that stops with its own exit code
        if e.code is None or isinstance(e.code, int):
            code = e.code or 0
        else:  # `SystemExit("message")`: a failure, and the message is the detail
            return False, f"{out.getvalue()}{e.code}"
    except Exception as e:  # noqa: BLE001 - a broken check is a failed check, not a crash of the doctor
        return False, f"{out.getvalue()}{type(e).__name__}: {e}"
    return not code, out.getvalue()


def _doctor(args: Sequence[str]) -> int:
    if args:
        print("usage: frc-mcp doctor   (it takes no options)", file=sys.stderr)
        return 0 if args[0] in ("-h", "--help") else 2
    keys = server_keys()
    print("frc-mcp doctor: looking at this computer only (no robot, no network).")
    passed = failed = skipped = 0
    for key in keys:
        try:
            skip = load_spec(key).doctor_skip
        except Exception as e:  # noqa: BLE001
            print(f"FAILED   {key}: cannot load it ({type(e).__name__}: {e})")
            failed += 1
            continue
        if skip:
            print(f"skipped  {key}: {skip}; run `frc-mcp {key} check`")
            skipped += 1
            continue
        ok, text = _run_check(key)
        if ok:
            print(f"ok       {key}")
            passed += 1
        else:
            print(f"FAILED   {key}  (details: `frc-mcp {key} check`)")
            lines = [line for line in text.splitlines() if line.strip()]
            for line in lines[:SHOWN_LINES]:
                print(f"           {line.strip()}")
            if len(lines) > SHOWN_LINES:
                print(f"           ... {len(lines) - SHOWN_LINES} more lines")
            failed += 1
    print(f"{passed + failed} checked: {passed} ok, {failed} failed. {skipped} skipped.")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    """The `frc-mcp` console script. Returns the exit code (a server that is serving returns 0 when it ends)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(help_text())
        return 0
    word, rest = args[0], args[1:]
    if word in ("-h", "--help", "help"):
        print(help_text())
        return 0
    if word == "doctor":
        return _doctor(rest)
    if word == "demo":
        return _demo(rest)
    if word == "hook":
        return _hook(rest)
    keys = server_keys()
    if word not in keys:
        return _unknown("command or server", word, [*keys, "doctor", "demo", "hook"])
    return load_spec(word).run(rest, prog=f"frc-mcp {word}") or 0

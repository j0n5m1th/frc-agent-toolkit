"""One declaration per server: a `ServerSpec` says what the server is, and builds, checks and serves it.

A server's `spec.py` holds its `SPEC`. Importing a spec is cheap: it does not import the tool modules or build the FastMCP server. Those wait for `create()` and
`load()`, so `check` and `--help` stay fast.
"""

import argparse
import importlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP

from .errors import FrcMcpError
from .processes import exit_on_signals
from .reply import response_budget
from .server import ApplyToolset, create_server, new_server
from .tools import SlimToolList
from .toolset import Toolset, ToolsetName, set_gate_visibility


def _no_toolset(value: str | None = None, /) -> ToolsetName:
    return "full"


@dataclass(frozen=True)
class ServerSpec:
    """What one server is. The fields are plain data; the methods do the work."""

    key: str  # short name, e.g. "jfr"
    name: str  # the FastMCP name, e.g. "jfr_mcp"
    description: str  # the command line's help text
    instructions: str | Callable[[bool], str]  # a sentence, or a function of `core` (needs a toolset)
    check: Callable[[argparse.Namespace], int]  # `<command> check`: print what it would use, return the exit code
    tool_modules: tuple[str, ...] = ()  # dotted names of the modules that register tools and prompts; imported only when serving
    toolset: Toolset | None = None  # the server's `*_TOOLSET` setting; None for a server with no core/full switch
    error: type[Exception] | tuple[type[Exception], ...] = ()  # what else stops serving with its message (a bad toolset value always does: `toolset.error`)
    extra_args: Callable[[argparse.ArgumentParser], None] | None = None  # more command line options (they reach `check` and `commands`)
    commands: Mapping[str, Callable[[argparse.Namespace], int]] = field(default_factory=dict)  # more subcommands next to `check`: name -> function returning the exit code
    prepare: Callable[[argparse.ArgumentParser, argparse.Namespace], None] | None = None  # runs after parsing, before any command or serving; may call `parser.error`
    slim: SlimToolList | None = None  # replaces the default lean-schema middleware
    on_toolset: Callable[[bool], None] | None = None  # runs on every toolset apply, for anything else that follows it
    gates: Mapping[str, Callable[[], bool]] = field(default_factory=dict)  # tool tag -> "is the switch on now?" (`capability_gates`); serving hides the tagged tools while it is off
    options: Mapping[str, Any] = field(default_factory=dict)  # more FastMCP options, e.g. lifespan
    doctor_skip: str = ""  # why `frc-mcp doctor` does not run `check` (it reaches the network or a device); "" means `check` is local and read-only, so doctor runs it
    _made: list[tuple[FastMCP, ApplyToolset]] = field(default_factory=list, init=False, repr=False, compare=False)

    def create(self) -> tuple[FastMCP, ApplyToolset]:
        """The server's FastMCP instance and its `apply_toolset`. Built once: a second call gives the same pair, so tools can register on it."""
        if not self._made:
            if self.toolset is None:
                if not isinstance(self.instructions, str):
                    raise TypeError(f"{self.key}: instructions that depend on the toolset need a toolset")
                self._made.append((new_server(self.name, self.instructions, slim=self.slim, **self.options), _no_toolset))
            else:
                self._made.append(create_server(self.name, self.instructions, self.toolset, slim=self.slim, on_toolset=self.on_toolset, **self.options))
        return self._made[0]

    def load(self) -> tuple[FastMCP, ApplyToolset]:
        """Import the tool modules (each registers its tools on the server) and return the server and its `apply_toolset`."""
        for module in self.tool_modules:
            importlib.import_module(module)
        return self.create()

    def apply_gates(self) -> dict[str, bool]:
        """Hide the tools of every gate whose switch is off, show the rest, and return each gate's state. Not part of `load()`: only serving does it.

        Every switch is read before anything changes, so a bad value raises its error (it must be an `error` of the spec) and leaves the tools as they were.
        """
        if not self.gates:
            return {}
        mcp, _ = self.create()
        states = {tag: is_on() for tag, is_on in self.gates.items()}
        set_gate_visibility(mcp, states, self.toolset is not None and self.toolset.current() == "core")
        return states

    def serve(self) -> None:
        """Run over stdio. A bad toolset or gate value (`error`, also when raised while loading) stops here with its message, before the server starts.

        Nothing else is caught, so a real bug keeps its traceback.
        """
        try:
            mcp, apply_toolset = self.load()
            apply_toolset()
            self.apply_gates()
            response_budget()  # a bad MCP_WPILIB_MAX_CHARS stops here, not at the first reply
        except (*self._errors(), FrcMcpError) as e:  # type: ignore[misc]
            raise SystemExit(str(e)) from e
        exit_on_signals()  # SIGTERM/SIGHUP end the server normally, so child processes are cleaned up
        mcp.run(show_banner=False)  # stdio by default

    def _errors(self) -> tuple[type[Exception], ...]:
        own = self.error if isinstance(self.error, tuple) else (self.error,) if self.error else ()
        return (*own, self.toolset.error) if self.toolset is not None else own

    def run(self, argv: Sequence[str] | None = None, prog: str | None = None) -> int | None:
        """The command line: no command serves (and returns None when the server ends); `check` (or one of `commands`) runs and returns its exit code.

        `argv` is what follows the command name (default: `sys.argv[1:]`); `prog` is the name usage messages show.
        """
        parser = argparse.ArgumentParser(prog=prog, description=self.description)
        parser.add_argument("command", nargs="?", choices=["check", *self.commands])
        if self.extra_args is not None:
            self.extra_args(parser)
        args = parser.parse_args(argv)
        if self.prepare is not None:
            self.prepare(parser, args)
        if args.command == "check":
            return self.check(args)
        if args.command in self.commands:
            return self.commands[args.command](args)
        self.serve()
        return None

    def main(self, argv: Sequence[str] | None = None, prog: str | None = None) -> None:
        """`run`, then exit with the code of a command that ran."""
        code = self.run(argv, prog)
        if code is not None:
            raise SystemExit(code)

"""`frc_mcp_core.spec.ServerSpec`: a server declared in one place, built, checked and served from it."""

import argparse
import sys

import pytest
from fastmcp import Client

from frc_mcp_core.spec import ServerSpec
from frc_mcp_core.toolset import Toolset


class Problem(Exception):
    pass


def make_spec(tmp_path, monkeypatch, *, toolset=None, check=lambda args: 0, extra_args=None, error=(), **more) -> ServerSpec:
    """A spec whose tool module is a tiny file written into `tmp_path`; the module reaches its server through the fake module `spec_under_test`."""
    package = "spec_probe_" + "".join(c for c in tmp_path.name if c.isalnum())  # unique per test
    (tmp_path / f"{package}.py").write_text(
        "import sys\n"
        "from frc_mcp_core.toolset import FULL_ONLY_TAG\n"
        "mcp, _ = sys.modules['spec_under_test'].SPEC.create()\n"
        "@mcp.tool()\ndef always() -> int:\n    return 1\n"
        "@mcp.tool(tags={FULL_ONLY_TAG})\ndef extra() -> int:\n    return 2\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    spec = ServerSpec(key="probe", name="probe_mcp", description="a test server", instructions="text", check=check, tool_modules=(package,),
                      toolset=toolset, extra_args=extra_args, error=error, **more)
    holder = type(sys)("spec_under_test")
    holder.SPEC = spec  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "spec_under_test", holder)
    return spec


def run_main(monkeypatch, spec: ServerSpec, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    spec.main()


async def tool_names(mcp) -> list[str]:
    async with Client(mcp) as client:
        return sorted(t.name for t in await client.list_tools())


def test_create_builds_the_server_once_and_a_toolset_hides_the_full_only_tools(tmp_path, monkeypatch):
    spec = make_spec(tmp_path, monkeypatch, toolset=Toolset("PROBE_TOOLSET", Problem))
    mcp, apply_toolset = spec.create()
    assert spec.create()[0] is mcp and mcp.name == "probe_mcp" and mcp.instructions == "text"
    assert apply_toolset("core") == "core" and apply_toolset("full") == "full"


async def test_load_imports_the_tool_modules_and_registers_their_tools(tmp_path, monkeypatch):
    spec = make_spec(tmp_path, monkeypatch, toolset=Toolset("PROBE_TOOLSET", Problem))
    assert spec.tool_modules[0] not in sys.modules  # building a spec imports no tool module
    mcp, apply_toolset = spec.load()
    assert await tool_names(mcp) == ["always", "extra"]
    apply_toolset("core")
    assert await tool_names(mcp) == ["always"]


async def test_a_spec_without_a_toolset_serves_every_tool(tmp_path, monkeypatch):
    spec = make_spec(tmp_path, monkeypatch)
    mcp, apply_toolset = spec.load()
    assert apply_toolset("core") == "full" and await tool_names(mcp) == ["always", "extra"]


def test_instructions_that_follow_the_toolset_need_one():
    spec = ServerSpec(key="k", name="n", description="d", instructions=lambda core: "x", check=lambda args: 0)
    with pytest.raises(TypeError, match="need a toolset"):
        spec.create()


def test_serve_applies_the_setting_then_runs_over_stdio(tmp_path, monkeypatch):
    spec = make_spec(tmp_path, monkeypatch, toolset=Toolset("PROBE_TOOLSET", Problem), error=Problem)
    monkeypatch.setenv("PROBE_TOOLSET", "core")
    mcp, _ = spec.create()
    started: list[dict] = []
    monkeypatch.setattr(mcp, "run", lambda **kwargs: started.append(kwargs))
    run_main(monkeypatch, spec)  # no SystemExit: main returns once the server stopped
    assert started == [{"show_banner": False}]
    assert spec.create()[0] is mcp and mcp.instructions == "text"


def test_a_bad_toolset_value_stops_with_its_message_before_the_server_starts(tmp_path, monkeypatch):
    spec = make_spec(tmp_path, monkeypatch, toolset=Toolset("PROBE_TOOLSET", Problem))  # the toolset's own error type is caught without naming it again
    mcp, _ = spec.create()
    monkeypatch.setattr(mcp, "run", lambda **kwargs: pytest.fail("must not run"))
    monkeypatch.setenv("PROBE_TOOLSET", "everything")
    with pytest.raises(SystemExit, match="PROBE_TOOLSET must be 'full' or 'core'"):
        spec.serve()


def test_a_real_bug_keeps_its_traceback_when_no_error_type_matches(tmp_path, monkeypatch):
    spec = ServerSpec(key="k", name="n", description="d", instructions="x", check=lambda args: 0, tool_modules=("module_that_does_not_exist_anywhere",))
    with pytest.raises(ModuleNotFoundError):
        spec.serve()


def test_check_runs_without_importing_the_tool_modules_or_serving(tmp_path, monkeypatch):
    seen: list[argparse.Namespace] = []

    def check(args: argparse.Namespace) -> int:
        seen.append(args)
        return 3

    def options(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--refresh", action="store_true")

    spec = make_spec(tmp_path, monkeypatch, check=check, extra_args=options)
    with pytest.raises(SystemExit) as stopped:
        run_main(monkeypatch, spec, "check", "--refresh")
    assert stopped.value.code == 3 and seen[0].refresh is True
    assert spec.tool_modules[0] not in sys.modules and spec._made == []  # no tool module imported, no server built


def test_an_unknown_command_is_refused_by_argparse(tmp_path, monkeypatch, capsys):
    spec = make_spec(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as stopped:
        run_main(monkeypatch, spec, "frobnicate")
    assert stopped.value.code == 2 and "invalid choice" in capsys.readouterr().err


def test_other_errors_named_in_error_also_stop_with_their_message(tmp_path, monkeypatch):
    spec = ServerSpec(key="k", name="n", description="d", instructions="x", check=lambda args: 0, tool_modules=("module_that_does_not_exist_anywhere",), error=ModuleNotFoundError)
    with pytest.raises(SystemExit, match="module_that_does_not_exist_anywhere"):
        spec.serve()


def test_more_commands_run_after_prepare_and_exit_with_their_code(tmp_path, monkeypatch):
    events: list[str] = []

    def prepare(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
        events.append(f"prepare:{args.command}")

    def init(args: argparse.Namespace) -> int:
        events.append(f"init:{args.host}")
        return 4

    def options(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--host")

    spec = make_spec(tmp_path, monkeypatch, check=lambda args: 0, extra_args=options, commands={"init": init}, prepare=prepare)
    with pytest.raises(SystemExit) as stopped:
        run_main(monkeypatch, spec, "init", "--host", "h")
    assert stopped.value.code == 4 and events == ["prepare:init", "init:h"]
    assert spec.tool_modules[0] not in sys.modules  # a command is not serving


def test_prepare_can_refuse_the_command_line_before_anything_runs(tmp_path, monkeypatch, capsys):
    def prepare(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
        parser.error("not like this")

    spec = make_spec(tmp_path, monkeypatch, check=lambda args: pytest.fail("must not run"), prepare=prepare)
    with pytest.raises(SystemExit) as stopped:
        run_main(monkeypatch, spec, "check")
    assert stopped.value.code == 2 and "not like this" in capsys.readouterr().err


def test_run_takes_the_argument_list_and_the_name_usage_shows_and_returns_the_exit_code(tmp_path, monkeypatch, capsys):
    seen: list[argparse.Namespace] = []

    def check(args: argparse.Namespace) -> int:
        seen.append(args)
        return 4

    def options(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--host")

    spec = make_spec(tmp_path, monkeypatch, check=check, extra_args=options)
    monkeypatch.setattr(sys, "argv", ["not", "used"])  # an explicit list wins over sys.argv
    assert spec.run(["check", "--host", "h"], prog="frc-mcp probe") == 4 and seen[0].host == "h"
    with pytest.raises(SystemExit):
        spec.run(["--help"], prog="frc-mcp probe")
    assert "usage: frc-mcp probe" in capsys.readouterr().out


def test_main_takes_the_argument_list_and_exits_with_the_code(tmp_path, monkeypatch):
    spec = make_spec(tmp_path, monkeypatch, check=lambda args: 5)
    with pytest.raises(SystemExit) as stopped:
        spec.main(["check"], prog="frc-mcp probe")
    assert stopped.value.code == 5


def test_a_spec_is_safe_for_doctor_unless_it_says_why_not(tmp_path, monkeypatch):
    assert make_spec(tmp_path, monkeypatch).doctor_skip == ""
    assert make_spec(tmp_path, monkeypatch, doctor_skip="it reaches a robot").doctor_skip == "it reaches a robot"


def test_a_bad_reply_size_setting_stops_the_server_at_start_up(tmp_path, monkeypatch):
    spec = make_spec(tmp_path, monkeypatch)
    mcp, _ = spec.create()
    monkeypatch.setattr(mcp, "run", lambda **kwargs: pytest.fail("must not run"))
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "lots")
    with pytest.raises(SystemExit, match="MCP_WPILIB_MAX_CHARS must be a number"):
        spec.serve()


def test_serving_takes_over_the_termination_signals(tmp_path, monkeypatch):
    spec = make_spec(tmp_path, monkeypatch)
    mcp, _ = spec.create()
    monkeypatch.setattr(mcp, "run", lambda **kwargs: None)
    called: list[bool] = []
    monkeypatch.setattr("frc_mcp_core.spec.exit_on_signals", lambda: called.append(True))
    spec.serve()
    assert called == [True]

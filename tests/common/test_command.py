"""The `frc-mcp` command: dispatch, help, `doctor` and `demo`, driven by fake entry points so no real server is touched."""

import importlib.metadata
import subprocess
import sys
import types

import pytest

from frc_mcp_core import command
from frc_mcp_core.spec import ServerSpec


def fake_entry_points(monkeypatch, servers: dict[str, ServerSpec | str] | None = None, demos: dict[str, object] | None = None) -> None:
    """Replace the installed entry points. A spec becomes the `SPEC` of a fresh fake module; a string is a module path that is never imported unless asked."""
    points: dict[str, list[importlib.metadata.EntryPoint]] = {command.SERVERS_GROUP: [], command.DEMOS_GROUP: []}
    for key, item in (servers or {}).items():
        if isinstance(item, str):
            value = item
        else:
            module = types.ModuleType(f"fake_server_{key.replace('-', '_')}")
            module.SPEC = item  # type: ignore[attr-defined]
            monkeypatch.setitem(sys.modules, module.__name__, module)
            value = f"{module.__name__}:SPEC"
        points[command.SERVERS_GROUP].append(importlib.metadata.EntryPoint(key, value, command.SERVERS_GROUP))
    for name, run in (demos or {}).items():
        module = types.ModuleType(f"fake_demo_{name}")
        module.main = run  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, module.__name__, module)
        points[command.DEMOS_GROUP].append(importlib.metadata.EntryPoint(name, f"{module.__name__}:main", command.DEMOS_GROUP))
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group=None, **_: points[group])


def spec(key: str, *, code: int = 0, text: str = "", skip: str = "", seen: list | None = None, serve=None) -> ServerSpec:
    def check(args) -> int:
        if seen is not None:
            seen.append((key, args.host))
        print(text or f"{key} looked fine")
        return code

    def extra(parser) -> None:
        parser.add_argument("--host", default="default-host")

    made = ServerSpec(key=key, name=f"{key}_mcp", description=f"the {key} server", instructions="text", check=check, extra_args=extra, doctor_skip=skip)
    if serve is not None:
        object.__setattr__(made, "serve", serve)  # the dataclass is frozen; a test server must not start stdio
    return made


def test_a_key_runs_that_server_and_forwards_the_rest_of_the_line(monkeypatch):
    seen: list = []
    fake_entry_points(monkeypatch, {"alpha": spec("alpha", seen=seen), "beta": spec("beta", code=3, seen=seen)})
    assert command.main(["alpha", "check", "--host", "robot.local"]) == 0
    assert command.main(["beta", "check"]) == 3  # the check's exit code is the command's
    assert seen == [("alpha", "robot.local"), ("beta", "default-host")]


def test_no_command_for_a_server_serves_it(monkeypatch):
    served: list[str] = []
    fake_entry_points(monkeypatch, {"alpha": spec("alpha", serve=lambda: served.append("alpha"))})
    assert command.main(["alpha"]) == 0
    assert served == ["alpha"]


def test_a_server_help_names_the_frc_mcp_command(monkeypatch, capsys):
    fake_entry_points(monkeypatch, {"alpha": spec("alpha")})
    with pytest.raises(SystemExit) as stop:
        command.main(["alpha", "--help"])
    assert stop.value.code == 0
    assert "usage: frc-mcp alpha" in capsys.readouterr().out


def test_an_unknown_key_lists_the_keys_and_exits_2(monkeypatch, capsys):
    fake_entry_points(monkeypatch, {"alpha": spec("alpha"), "beta": spec("beta")})
    assert command.main(["nope"]) == 2
    err = capsys.readouterr().err
    assert "'nope'" in err and "alpha, beta" in err and "doctor" in err


def test_help_lists_the_keys_and_demos_without_importing_any_spec(monkeypatch, capsys):
    fake_entry_points(monkeypatch, {"beta": "never_imported_beta.spec:SPEC", "alpha": "never_imported_alpha.spec:SPEC"},
                      demos={"robot": lambda argv: 0})
    for argv in ([], ["--help"], ["-h"], ["help"]):
        assert command.main(argv) == 0
        out = capsys.readouterr().out
        assert "servers: alpha, beta" in out and "frc-mcp doctor" in out and "demos:   robot" in out


def test_help_on_the_real_install_is_fast_and_imports_no_server():
    code = (
        "import sys\n"
        "from frc_mcp_core.command import main\n"
        "assert main(['--help']) == 0\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('frc_mcp_') and m not in ('frc_mcp_core', 'frc_mcp_core.command'))\n"
        "assert loaded == [], loaded\n"
        "assert 'fastmcp' not in sys.modules and 'ntcore' not in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    for key in ("nt4", "wpilog", "sim", "vscode", "systemcore", "jfr", "ctre-tools", "ctre-owlet", "ctre-passerine", "ctre-caniv", "ctre-diag"):
        assert key in done.stdout
    assert "demos:   log, robot" in done.stdout


def test_doctor_runs_the_safe_checks_in_key_order_and_skips_the_rest(monkeypatch, capsys):
    seen: list = []
    fake_entry_points(monkeypatch, {
        "zeta": spec("zeta", seen=seen),
        "net": spec("net", skip="it talks to a robot", seen=seen),
        "alpha": spec("alpha", seen=seen),
    })
    assert command.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert [key for key, _ in seen] == ["alpha", "zeta"]  # skipped checks never run
    assert out.index("ok       alpha") < out.index("skipped  net") < out.index("ok       zeta")
    assert "skipped  net: it talks to a robot; run `frc-mcp net check`" in out
    assert "2 checked: 2 ok, 0 failed. 1 skipped." in out


def test_a_failing_check_does_not_stop_the_others_and_makes_the_exit_code_non_zero(monkeypatch, capsys):
    seen: list = []

    def boom(_args):
        raise RuntimeError("it broke")

    broken = spec("broken")
    object.__setattr__(broken, "check", boom)
    fake_entry_points(monkeypatch, {"a-fails": spec("a-fails", code=1, text="no project found", seen=seen), "b-broken": broken,
                                    "c-ok": spec("c-ok", seen=seen)})
    assert command.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAILED   a-fails" in out and "no project found" in out and "FAILED   b-broken" in out and "RuntimeError: it broke" in out
    assert "ok       c-ok" in out and ("c-ok", "default-host") in seen
    assert "3 checked: 1 ok, 2 failed." in out


def test_doctor_with_only_skipped_servers_passes(monkeypatch, capsys):
    fake_entry_points(monkeypatch, {"net": spec("net", skip="it talks to a robot")})
    assert command.main(["doctor"]) == 0
    assert "0 checked: 0 ok, 0 failed. 1 skipped." in capsys.readouterr().out


def test_doctor_takes_no_options(monkeypatch, capsys):
    fake_entry_points(monkeypatch, {"alpha": spec("alpha")})
    assert command.main(["doctor", "--host", "x"]) == 2
    assert "takes no options" in capsys.readouterr().err


def test_a_server_that_cannot_be_loaded_is_a_failure_not_a_crash(monkeypatch, capsys):
    fake_entry_points(monkeypatch, {"alpha": "module_that_does_not_exist_anywhere:SPEC", "beta": spec("beta")})
    assert command.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAILED   alpha: cannot load it" in out and "ok       beta" in out


def test_demo_runs_the_named_demo_with_the_rest_of_the_line(monkeypatch):
    calls: list = []
    fake_entry_points(monkeypatch, demos={"robot": lambda argv: calls.append(("robot", argv)) or 5, "log": lambda argv: calls.append(("log", argv))})
    assert command.main(["demo", "robot", "--port", "5999"]) == 5
    assert command.main(["demo", "log"]) == 0  # a demo that returns nothing succeeded
    assert calls == [("robot", ["--port", "5999"]), ("log", [])]


def test_demo_without_a_name_lists_the_demos_and_an_unknown_name_exits_2(monkeypatch, capsys):
    fake_entry_points(monkeypatch, demos={"robot": lambda argv: 0, "log": lambda argv: 0})
    assert command.main(["demo"]) == 2
    assert "demos: log, robot" in capsys.readouterr().out
    assert command.main(["demo", "--help"]) == 0
    capsys.readouterr()
    assert command.main(["demo", "nope"]) == 2
    assert "'nope'" in capsys.readouterr().err


def test_a_check_that_exits_cleanly_passes_and_one_that_exits_with_a_message_shows_it(monkeypatch, capsys):
    def quiet_exit(_args):
        raise SystemExit

    def exit_zero(_args):
        raise SystemExit(0)

    def exit_message(_args):
        raise SystemExit("settings file is unreadable")

    specs = {}
    for key, check in (("a-bare", quiet_exit), ("b-zero", exit_zero), ("c-msg", exit_message)):
        made = spec(key)
        object.__setattr__(made, "check", check)
        specs[key] = made
    fake_entry_points(monkeypatch, specs)
    assert command.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "ok       a-bare" in out and "ok       b-zero" in out
    assert "FAILED   c-msg" in out and "settings file is unreadable" in out

"""Command line dispatch: argument validation, `check`, and the default stdio server."""

import pytest

from frc_mcp_nt4 import diagnostics, spec

mcp, _ = spec.SPEC.load()


def _run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["mcp-wpilib-nt4", *argv])
    spec.main()


def _stub_diagnose(monkeypatch, code=0):
    calls = []

    async def fake_diagnose(host, port, timeout):
        calls.append((host, port, timeout))
        return code

    monkeypatch.setattr(diagnostics, "diagnose", fake_diagnose)
    return calls


def _stub_run(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp, "run", lambda **kwargs: calls.append(kwargs))
    return calls


def test_unknown_command_is_an_argparse_error(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "bogus")
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("timeout", ["0", "-1", "61", "nan", "inf"])
def test_check_rejects_out_of_range_timeouts(monkeypatch, capsys, timeout):
    calls = _stub_diagnose(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "check", "--timeout", timeout)
    assert exc.value.code == 2 and not calls
    assert "--timeout must be greater than 0" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [["--host", "10.0.0.2"], ["--port", "5810"], ["--timeout", "3"]])
def test_connection_flags_need_check(monkeypatch, capsys, flags):
    calls = _stub_run(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, *flags)
    assert exc.value.code == 2 and not calls
    assert "only supported with check" in capsys.readouterr().err


def test_non_numeric_port_is_an_argparse_error(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "check", "--port", "abc")
    assert exc.value.code == 2
    assert "--port" in capsys.readouterr().err


def test_check_passes_arguments_and_exits_with_its_status(monkeypatch):
    calls = _stub_diagnose(monkeypatch, code=3)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "check", "--host", "10.0.0.2", "--port", "5811", "--timeout", "2.5")
    assert exc.value.code == 3
    assert calls == [("10.0.0.2", 5811, 2.5)]


def test_check_defaults_to_five_seconds_and_environment_host(monkeypatch):
    calls = _stub_diagnose(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "check")
    assert exc.value.code == 0
    assert calls == [(None, None, 5.0)]


def test_no_command_runs_the_stdio_server_without_a_banner(monkeypatch):
    calls = _stub_run(monkeypatch)
    _run(monkeypatch)
    assert calls == [{"show_banner": False}]


def test_unknown_toolset_exits_before_the_server_starts(monkeypatch):
    calls = _stub_run(monkeypatch)
    monkeypatch.setenv("NT4_TOOLSET", "everything")
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch)
    assert "NT4_TOOLSET" in str(exc.value) and not calls

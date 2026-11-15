"""systemcore_provision: the switch, the confirmed host key, and passwords that stay out of replies."""


import pytest
from envelope_helpers import j
from fastmcp.exceptions import ToolError

from frc_mcp_systemcore import net, ssh
from frc_mcp_systemcore import state as server_state
from frc_mcp_systemcore.errors import HostKeyChanged, NeedsProvisioning, SystemcoreError

SECRET = "s3cret&<pw>"
FINGERPRINT = "SHA256:" + "A" * 43


async def call(client, name, args=None):
    return j(await client.call_tool(name, args or {}))


class Opened:
    def close(self):
        pass


@pytest.fixture
def provisioning(monkeypatch, device):
    """The fake device answers HTTP; connect_sync fails until install_key_sync has run (as on a controller without the key)."""
    state = {"installed": False, "passwords": []}

    def connect(host, os_hash=None, timeout=None):
        if not state["installed"]:
            raise NeedsProvisioning("not installed")
        return Opened()

    def install(host, password, timeout=None, os_hash=None):
        state["passwords"].append(password)
        if password != "right":
            raise SystemcoreError("The Systemcore refused the saved login password. Set SYSTEMCORE_PASSWORD ...")
        state["installed"] = True
        return "SHA256:pinned"

    monkeypatch.setenv("SYSTEMCORE_ALLOW_PROVISION", "1")
    monkeypatch.setattr(ssh, "connect_sync", connect)
    monkeypatch.setattr(ssh, "install_key_sync", install)
    monkeypatch.setattr(ssh, "probe_host_key", lambda host, timeout=None: FINGERPRINT)
    return state


async def test_provision_is_off_until_the_owner_allows_it(client, provisioning, monkeypatch):
    monkeypatch.delenv("SYSTEMCORE_ALLOW_PROVISION")
    with pytest.raises(ToolError, match="SYSTEMCORE_ALLOW_PROVISION=1"):
        await client.call_tool("systemcore_provision", {})
    assert provisioning["passwords"] == []


async def test_a_custom_password_is_never_used_from_a_tool_call(client, provisioning, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_PASSWORD", "right")
    with pytest.raises(ToolError, match="frc-mcp systemcore provision") as error:
        await client.call_tool("systemcore_provision", {})
    assert "right" not in str(error.value) and provisioning["passwords"] == []  # nothing sent
    server_state.pin("127.0.0.1", FINGERPRINT, None, confirmed=True)  # even a host key the user confirmed earlier does not allow it
    with pytest.raises(ToolError, match="frc-mcp systemcore provision"):
        await client.call_tool("systemcore_provision", {})
    assert provisioning["passwords"] == []
    with pytest.raises(ToolError):
        await client.call_tool("systemcore_provision", {"fingerprint": FINGERPRINT})  # there is no such argument any more
    assert provisioning["passwords"] == []


async def test_an_installed_key_needs_nothing_even_with_a_custom_password(client, provisioning, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_PASSWORD", "right")
    provisioning["installed"] = True
    assert (await call(client, "systemcore_provision"))["already_installed"] is True and provisioning["passwords"] == []


def test_the_provision_command_confirms_the_key_at_the_terminal_before_a_custom_password(monkeypatch, provisioning, capsys, tmp_path):
    from frc_mcp_systemcore import spec

    monkeypatch.setenv("SYSTEMCORE_PASSWORD", "right")
    monkeypatch.setenv("SYSTEMCORE_HOST", "127.0.0.1")
    args = spec.argparse.Namespace(target=None, host=None, ask=False, fingerprint=None)
    monkeypatch.setattr(spec, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "wrongwro")
    assert spec._provision(args) == 1 and provisioning["passwords"] == []  # a wrong confirmation sends nothing
    monkeypatch.setattr(spec, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": FINGERPRINT.removeprefix("SHA256:")[:8])
    assert spec._provision(args) == 1 and provisioning["passwords"] == []  # not a terminal: refused whatever is typed
    monkeypatch.setattr(spec, "_stdin_is_tty", lambda: True)
    assert spec._provision(args) == 0 and provisioning["passwords"] == ["right"]
    assert server_state.pinned("127.0.0.1")["confirmed"] is True
    assert "right" not in capsys.readouterr().out


def test_the_trust_command_pins_only_after_the_typed_confirmation(monkeypatch, device, capsys):
    from frc_mcp_systemcore import spec

    monkeypatch.setenv("SYSTEMCORE_HOST", "127.0.0.1")
    monkeypatch.setattr(ssh, "probe_host_key", lambda host, timeout=None: FINGERPRINT)
    server_state.pin("127.0.0.1", "SHA256:" + "D" * 43, "oldhash")
    args = spec.argparse.Namespace(target="127.0.0.1", host=None, ask=False, fingerprint=None)
    monkeypatch.setattr(spec, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": FINGERPRINT.removeprefix("SHA256:")[:8])
    assert spec._trust(args) == 1 and server_state.pinned("127.0.0.1")["fingerprint"] == "SHA256:" + "D" * 43
    monkeypatch.setattr(spec, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "nope")
    assert spec._trust(args) == 1 and server_state.pinned("127.0.0.1")["fingerprint"] == "SHA256:" + "D" * 43
    out = capsys.readouterr().out
    assert FINGERPRINT in out and "OS image changed" in out
    args.fingerprint = "SHA256:" + "E" * 43  # expected another key: stop before asking
    assert spec._trust(args) == 1
    args.fingerprint = FINGERPRINT
    monkeypatch.setattr("builtins.input", lambda prompt="": FINGERPRINT.removeprefix("SHA256:")[:8])
    assert spec._trust(args) == 0
    pin = server_state.pinned("127.0.0.1")
    assert pin["fingerprint"] == FINGERPRINT and pin["confirmed"] is True and pin["os_hash"] == "0a1b2c3d"
    assert spec._trust(args) == 0 and "already trusted" in capsys.readouterr().out


async def test_provision_with_the_default_password_needs_no_confirmation(client, provisioning, monkeypatch):
    monkeypatch.delenv("SYSTEMCORE_PASSWORD", raising=False)
    with pytest.raises(ToolError, match="SYSTEMCORE_PASSWORD"):
        await client.call_tool("systemcore_provision", {})
    assert provisioning["passwords"] == ["systemcore"]  # the documented default was tried, once


async def test_provision_over_a_tool_does_not_repeat_a_custom_password(client, provisioning, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_PASSWORD", SECRET)
    server_state.pin("127.0.0.1", FINGERPRINT, None, confirmed=True)
    with pytest.raises(ToolError) as error:
        await client.call_tool("systemcore_provision", {})
    assert SECRET not in str(error.value) and provisioning["passwords"] == []


def test_the_provision_command_reports_a_refused_password_without_repeating_it(monkeypatch, provisioning, capsys):
    from frc_mcp_systemcore import spec

    monkeypatch.setenv("SYSTEMCORE_PASSWORD", SECRET)
    monkeypatch.setenv("SYSTEMCORE_HOST", "127.0.0.1")
    server_state.pin("127.0.0.1", FINGERPRINT, None, confirmed=True)
    assert spec._provision(spec.argparse.Namespace(target=None, host=None, ask=False, fingerprint=None)) == 1
    shown = capsys.readouterr().out
    assert SECRET not in shown and "refused" in shown
    assert provisioning["passwords"] == [SECRET]  # the user confirmed the key, so it was tried once


async def test_provision_sends_no_password_to_a_host_that_is_not_a_systemcore(client, provisioning, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_PASSWORD", "right")
    monkeypatch.setenv("SYSTEMCORE_TIMEOUT_S", "1")
    monkeypatch.setitem(net.PORTS, "update", 9)  # nothing listens here
    with pytest.raises(ToolError, match="did not answer like a Systemcore"):
        await client.call_tool("systemcore_provision", {})
    assert provisioning["passwords"] == []


async def test_provision_stops_on_a_changed_host_key(client, provisioning, monkeypatch):
    def changed(host, os_hash=None, timeout=None):
        raise HostKeyChanged("The SSH host key of 127.0.0.1 changed")

    monkeypatch.setattr(ssh, "connect_sync", changed)
    with pytest.raises(ToolError, match="host key"):
        await client.call_tool("systemcore_provision", {})
    assert provisioning["passwords"] == []

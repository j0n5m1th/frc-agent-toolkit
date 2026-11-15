"""SSH and SFTP to the controller with a pinned host key and the key this server owns. No password is used here.

Host key: the first connection to an address pins its key. A later mismatch stops with HostKeyChanged (expected after an OS
update, which brings new keys); it is accepted only at a terminal, by the user typing part of the fingerprint
(`frc-mcp systemcore trust <host>`, or `provision`). No tool accepts a host key, so the model cannot do it for the user.
Login: only the managed key is offered; if refused, NeedsProvisioning says to call systemcore_provision.
Connections go to the IP checked by `net.connect_address` (resolved once, every address private), never to the name again, so a
DNS answer that changes in between cannot redirect them. Pins stay keyed by the name the user gave.
"""

# paramiko is annotations only until a connection is made
from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from . import config, net, state
from .errors import HostKeyChanged, NeedsProvisioning, SystemcoreError

if TYPE_CHECKING:
    import paramiko
else:

    class _LazyParamiko:
        """`paramiko` imported on first use: it costs about 170 ms and 9 MB, and most sessions never open an SSH connection."""

        def __getattr__(self, name: str) -> Any:
            import paramiko as module

            return getattr(module, name)

    paramiko = _LazyParamiko()

MAX_OUTPUT = 1_000_000  # bytes of a command's output kept
CHUNK = 32 * 1024


def fingerprint(key: paramiko.PKey) -> str:
    return state.fingerprint_of_blob(key.asbytes())


def probe_host_key(host: str, timeout: float | None = None) -> str:
    """The fingerprint the device presents now, without logging in."""
    transport = None
    sock = None
    wait = timeout or config.timeout_s()
    try:
        sock = socket.create_connection((net.connect_address(host), net.port("ssh")), timeout=wait)  # `Transport((host, port))` has no connect timeout
        transport = paramiko.Transport(sock)
        transport.banner_timeout = wait
        transport.start_client(timeout=wait)
        return fingerprint(transport.get_remote_server_key())
    except (OSError, paramiko.SSHException, EOFError) as e:
        raise SystemcoreError(f"No SSH server answered at {host}:{net.port('ssh')} ({e}).") from e
    finally:
        if transport is not None:
            transport.close()
        if sock is not None:
            sock.close()


class _PinPolicy:  # paramiko only calls `missing_host_key`, so it need not subclass `MissingHostKeyPolicy` (that would import paramiko here)
    def __init__(self, host: str, os_hash: str | None) -> None:
        self.host, self.os_hash, self.newly_pinned = host, os_hash, False

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        actual = fingerprint(key)
        known = state.pinned(self.host)
        if known is None:
            state.pin(self.host, actual, self.os_hash)
            self.newly_pinned = True
        elif known["fingerprint"] != actual:
            raise HostKeyChanged(host_key_message(self.host, known, actual, self.os_hash))


def os_change_note(known: dict[str, Any], os_hash: str | None) -> str:
    """Whether the OS image changed since the key was pinned (a new key is expected then), in one sentence."""
    changed_os = bool(os_hash and known.get("os_hash") and os_hash != known["os_hash"])
    return ("The OS image changed since it was pinned, so a new key is expected." if changed_os
            else "The OS image did not visibly change, which is unusual: treat it as suspicious until the user confirms.")


def host_key_message(host: str, known: dict[str, Any], actual: str, os_hash: str | None) -> str:
    return (f"The SSH host key of {host} changed (pinned {known['fingerprint']}, now {actual}). {os_change_note(known, os_hash)} Only the user can trust "
            f"the new key: tell them to run `frc-mcp systemcore trust {host}` in a terminal. Nothing here can accept it.")


class Session:
    """One open SSH connection. Not thread-safe; used from one worker at a time."""

    def __init__(self, client: paramiko.SSHClient, host: str, newly_pinned: bool) -> None:
        self.client, self.host, self.newly_pinned = client, host, newly_pinned
        self._sftp: paramiko.SFTPClient | None = None

    @property
    def sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            self._sftp = self.client.open_sftp()
        return self._sftp

    def run(self, command: str, timeout: float = 30) -> tuple[int, str, str]:
        """Run one fixed command line; output is capped. Callers never build `command` from free text."""
        _, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        out = stdout.read(MAX_OUTPUT).decode("utf-8", "replace")
        err = stderr.read(MAX_OUTPUT // 10).decode("utf-8", "replace")
        return stdout.channel.recv_exit_status(), out, err

    def listdir(self, path: str) -> list[paramiko.SFTPAttributes]:
        return self.sftp.listdir_attr(path)

    def get(self, remote: str, local: Path) -> int:
        """Copy one file, resuming a partial `.part` copy; the finished file replaces `local`. Returns the bytes in the file."""
        size = self.sftp.stat(remote).st_size or 0
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_suffix(local.suffix + ".part")
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            have = 0
        with self.sftp.open(remote, "rb") as source, part.open("r+b" if have else "wb") as out:
            if have:
                source.seek(have)
                out.seek(have)
            while chunk := source.read(CHUNK):
                out.write(chunk)
        if part.stat().st_size != size:
            raise SystemcoreError(f"{remote} copied {part.stat().st_size} of {size} bytes (the file changed while copying; try again).")
        part.replace(local)
        return size

    def remove(self, remote: str) -> None:
        self.sftp.remove(remote)

    def rmdir(self, remote: str) -> None:
        self.sftp.rmdir(remote)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._sftp is not None:
                self._sftp.close()
        self.client.close()


def _new_client(host: str, os_hash: str | None) -> tuple[paramiko.SSHClient, _PinPolicy]:
    policy = _PinPolicy(host, os_hash)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(cast("paramiko.MissingHostKeyPolicy", policy))
    return client, policy


def _login(client: paramiko.SSHClient, host: str, wait: float, **auth: Any) -> None:
    """Connect with exactly one kind of credential (`pkey` or `password`): never the agent, never keys found on disk."""
    client.connect(net.connect_address(host), port=net.port("ssh"), username=config.user(), look_for_keys=False, allow_agent=False,
                   timeout=wait, banner_timeout=wait, auth_timeout=wait, **auth)


def check_login(host: str, os_hash: str | None = None) -> None:
    """Log in with the managed key and log out again: raises NeedsProvisioning, HostKeyChanged or SystemcoreError."""
    connect_sync(host, os_hash).close()


def connect_sync(host: str, os_hash: str | None = None, timeout: float | None = None) -> Session:
    wait = timeout or config.timeout_s()
    try:
        key = paramiko.Ed25519Key.from_private_key_file(str(state.ensure_key()))
    except (OSError, paramiko.SSHException) as e:
        raise SystemcoreError(f"Cannot read this server's SSH key in {config.state_dir()}: {e}") from e
    client, policy = _new_client(host, os_hash)
    try:
        _login(client, host, wait, pkey=key)
    except paramiko.AuthenticationException as e:
        client.close()
        raise NeedsProvisioning(
            f"{host} refused this server's SSH key (a new OS image or a first setup). Ask the user to confirm, then call "
            "systemcore_provision: it installs the key with the default login password. With a custom password the user runs "
            "`frc-mcp systemcore provision` in a terminal.") from e
    except (HostKeyChanged, SystemcoreError):
        client.close()
        raise
    except (OSError, paramiko.SSHException, EOFError) as e:
        client.close()
        raise SystemcoreError(f"SSH to {host} failed: {e}") from e
    return Session(client, host, policy.newly_pinned)


@contextlib.asynccontextmanager
async def session(host: str, os_hash: str | None = None) -> AsyncIterator[Session]:
    """Open a connection in a worker thread and close it afterwards. Calls on the session run through `to_thread`."""
    opened = await asyncio.to_thread(connect_sync, host, os_hash)
    try:
        yield opened
    finally:
        await asyncio.to_thread(opened.close)


def require_confirmed_host_key(host: str, os_hash: str | None, fingerprint: str | None) -> None:
    """Raise unless the pinned host key was confirmed by the user, or `fingerprint` (confirmed by the user at a terminal) matches the key
    presented now, which then becomes the confirmed pin. A key that differs from the pinned one raises HostKeyChanged, as everywhere else."""
    presented = probe_host_key(host)
    pin = state.pinned(host)
    if pin is not None and pin["fingerprint"] != presented:
        raise HostKeyChanged(host_key_message(host, pin, presented, os_hash))
    if pin is not None and pin.get("confirmed"):
        return
    if fingerprint != presented:
        raise SystemcoreError(f"A custom password is only sent to a host key the user has confirmed at a terminal, and {host} has not been confirmed yet. "
                              f"Tell the user to run `frc-mcp systemcore provision --host {host}` in a terminal.")
    state.pin(host, presented, os_hash, confirmed=True)


def provision_sync(host: str, os_hash: str | None, *, password: str | None = None, fingerprint: str | None = None) -> dict[str, Any]:
    """Make the managed key log in: nothing to do if it already does, else install it with the login password.
    The password (default: the saved one) is `systemcore` or the user's own. The user's own is sent only after the user
    confirmed the host key at a terminal (`fingerprint`); a tool call passes none, so a custom password is refused there.
    """
    try:
        check_login(host, os_hash)
        return {"host": host, "login": "ok", "already_installed": True}
    except NeedsProvisioning:
        pass
    secret = config.password() if password is None else password
    if secret != config.DEFAULT_PASSWORD:
        if fingerprint is None:
            raise SystemcoreError(f"A custom login password is not used from the assistant. Tell the user to run `frc-mcp systemcore provision --host {host}` in a terminal.")
        require_confirmed_host_key(host, os_hash, fingerprint)
    pinned = install_key_sync(host, secret, None, os_hash)
    check_login(host, os_hash)
    return {"host": host, "user": config.user(), "host_key": pinned, "login": "ok",
            "hint": "Key installed. The host key was trusted on first sight."}


def install_key_sync(host: str, password: str, timeout: float | None = None, os_hash: str | None = None) -> str:
    """Log in once with a password and add the managed public key to authorized_keys. Returns the pinned fingerprint.

    Only `provision_sync` calls this, with the password from the server's settings (or typed by the user at `provision --ask`). It is never stored by this module, logged or returned.
    """
    wait = timeout or config.timeout_s()
    client, _ = _new_client(host, os_hash)
    try:
        _login(client, host, wait, password=password)
        line = state.public_key_line()
        # Idempotent: add the line only when it is not already there; keep the file private.
        command = ("mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
                   f"(grep -qF '{line}' ~/.ssh/authorized_keys || echo '{line}' >> ~/.ssh/authorized_keys)")
        _, stdout, _ = client.exec_command(command, timeout=wait)
        if stdout.channel.recv_exit_status() != 0:
            raise SystemcoreError("Could not write authorized_keys on the Systemcore.")
    except paramiko.AuthenticationException as e:
        raise SystemcoreError("The Systemcore refused the saved login password. Set SYSTEMCORE_PASSWORD in the server's settings file (`mcp-systemcore check` shows where) and restart the server; it is never taken from chat.") from e
    except (OSError, paramiko.SSHException, EOFError) as e:
        raise SystemcoreError(f"SSH to {host} failed: {e}") from e
    finally:
        client.close()
    return (state.pinned(host) or {}).get("fingerprint", "")

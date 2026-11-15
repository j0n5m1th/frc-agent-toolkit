"""This server's own folder: its SSH key, pinned host keys, remembered devices and a download record.
An OS update replaces the Systemcore's root filesystem, so the installed key and host key change. Keeping both here (not in
~/.ssh, not in the repo) lets the server notice that and say what to do, and never stores a password.
"""

import base64
import contextlib
import hashlib
import ipaddress
import json
import os
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from frc_mcp_wpilib.files import write_atomic

from . import config
from .errors import SystemcoreError

KEY_NAME = "id_ed25519"
COMMENT = "mcp-systemcore"


def _file(name: str) -> Path:
    return config.state_dir() / name


def load(name: str) -> dict[str, Any]:
    try:
        data = json.loads(_file(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(name: str, data: dict[str, Any]) -> None:
    path = _file(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(data, indent=1, sort_keys=True))
    except OSError as e:
        raise SystemcoreError(f"Cannot write {path}: {e}. Check that folder is writable (from a clone, you can set SYSTEMCORE_STATE_DIR).") from e


def key_path() -> Path:
    return _file(KEY_NAME)


def ensure_key() -> Path:
    """The private key file, created (ed25519, owner-only) on first use."""
    path = key_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    private = ed25519.Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    try:  # created owner-only from the start, so the key is never readable by others, not even for a moment
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:  # another process made it between the check and here
        return path
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(pem)
    except BaseException:
        with contextlib.suppress(OSError):  # never leave a broken key behind
            path.unlink()
        raise
    return path


def public_key_line() -> str:
    """The `ssh-ed25519 AAAA... comment` line to install in the controller's authorized_keys."""
    private = serialization.load_ssh_private_key(ensure_key().read_bytes(), password=None)
    public = private.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)  # type: ignore[union-attr]
    return f"{public.decode()} {COMMENT}"


def fingerprint_of_blob(blob: bytes) -> str:
    """The `SHA256:...` fingerprint of a public key's wire bytes (what ssh-keygen -l and the host key checks show)."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def fingerprint_of_public_line(line: str) -> str:
    return fingerprint_of_blob(base64.b64decode(line.split()[1]))


def pin_key(host: str) -> str:
    """The key a host's pin is stored under: one spelling for one address (`[fe80::1]`, `FE80::1` and `fe80::1%usb0` are the same), a name in lower case without a final dot."""
    text = host.strip()
    inner = text[1:-1] if text.startswith("[") and text.endswith("]") else text
    try:
        return ipaddress.ip_address(inner.split("%", 1)[0]).compressed
    except ValueError:
        return text.lower().rstrip(".")


def pinned(host: str) -> dict[str, Any] | None:
    hosts = load("hosts.json")
    entry = hosts.get(pin_key(host))
    if entry is None:
        entry = hosts.get(host)  # a pin an earlier version stored under the spelling as typed
    return entry if isinstance(entry, dict) else None


def pin(host: str, fingerprint: str, os_hash: str | None = None, confirmed: bool = False) -> None:
    """Pin a host key. `confirmed` means the user checked this fingerprint; a first-sight pin is not confirmed."""
    hosts = load("hosts.json")
    hosts.pop(host, None)  # the old spelling, if an earlier version stored one
    hosts[pin_key(host)] = {"fingerprint": fingerprint, "os_hash": os_hash, "pinned_at": int(time.time()), "confirmed": confirmed}
    save("hosts.json", hosts)


def forget(host: str) -> bool:
    hosts = load("hosts.json")
    existed = hosts.pop(pin_key(host), None) is not None
    existed = hosts.pop(host, None) is not None or existed
    save("hosts.json", hosts)
    return existed


def remember_device(host: str, **facts: Any) -> None:
    """Record what was learned about a device and make it the default one. A failure to save is not worth failing a tool for."""
    with contextlib.suppress(SystemcoreError):
        devices = load("devices.json")
        entry = devices.get("devices", {}).get(host, {})
        entry.update({k: v for k, v in facts.items() if v is not None})
        entry["last_seen"] = int(time.time())
        devices.setdefault("devices", {})[host] = entry
        devices["last_host"] = host
        save("devices.json", devices)


def last_host() -> str | None:
    value = load("devices.json").get("last_host")
    return value if isinstance(value, str) else None


def device(host: str) -> dict[str, Any]:
    entry = load("devices.json").get("devices", {}).get(host)
    return entry if isinstance(entry, dict) else {}


def record_downloads(sizes: dict[str, int]) -> None:
    """Remember the size of each downloaded file (name -> bytes), in one write."""
    save("downloads.json", {**load("downloads.json"), **sizes})


def record_download(name: str, size: int) -> None:
    downloads = load("downloads.json")
    downloads[name] = size
    save("downloads.json", downloads)


def downloaded(name: str) -> int | None:
    value = load("downloads.json").get(name)
    return value if isinstance(value, int) else None

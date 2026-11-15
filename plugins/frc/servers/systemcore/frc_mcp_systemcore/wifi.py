"""Joining a Wi-Fi network from this computer, so first contact needs no clicking. Windows uses `netsh wlan`, Linux uses `nmcli`.
macOS can read the current network but not join: its only command puts the password on the command line, where any process could
read it. The password comes only from the server's settings, reaches disk only as a short-lived profile file in a private
temporary folder on Windows (deleted when the call ends, even on error; a profile from a failed join is removed), and is
removed from any text sent back.
"""

import contextlib
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

from .errors import SystemcoreError

_NETSH_SSID = re.compile(r"^[ 	]*SSID[ 	]*:[ 	]*(.+?)[ 	]*$", re.MULTILINE)  # not BSSID: that line starts with a B
_NETSH_SCAN = re.compile(r"^[ 	]*SSID[ 	]+\d+[ 	]*:[ 	]*(.*?)[ 	]*$", re.MULTILINE)  # one line: a hidden network has an empty name
_PROFILE = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM><security>
    <authEncryption><authentication>WPA2PSK</authentication><encryption>AES</encryption><useOneX>false</useOneX></authEncryption>
    <sharedKey><keyType>passPhrase</keyType><protected>false</protected><keyMaterial>{password}</keyMaterial></sharedKey>
  </security></MSM>
</WLANProfile>
"""


def platform() -> str:
    return sys.platform


def _run(args: list[str], stdin: str | None = None, timeout: float = 30) -> tuple[int, str]:
    """Run one command without a shell; `(exit code, output)`. A missing program or a timeout is a SystemcoreError."""
    try:
        done = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SystemcoreError(f"Could not run {args[0]}: {e}") from e
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def current_ssid() -> str | None:
    """The Wi-Fi network this computer is on now, or None (not on Wi-Fi, or no way to tell)."""
    system = platform()
    if system.startswith("win"):
        code, out = _run(["netsh", "wlan", "show", "interfaces"])
        found = _NETSH_SSID.search(out) if code == 0 else None
        return found[1] if found else None
    if system.startswith("linux"):
        code, out = _run(["nmcli", "-t", "-f", "active,ssid", "device", "wifi"])
        for line in out.splitlines() if code == 0 else []:
            if line.startswith("yes:"):
                return line[4:].replace("\\:", ":") or None
        return None
    if system == "darwin":
        code, out = _run(["networksetup", "-getairportnetwork", "en0"])
        return out.split(":", 1)[1].strip() if code == 0 and ": " in out and "not associated" not in out.lower() else None
    return None


MAX_PROFILES_CHECKED = 30


def _windows_profile_names() -> list[str]:
    """The names of the Wi-Fi profiles saved on this Windows computer."""
    code, out = _run(["netsh", "wlan", "show", "profiles"])
    return re.findall(r"^[ \t]*(?:All|Current) User Profile[ \t]*:[ \t]*(.+?)[ \t]*$", out, re.MULTILINE) if code == 0 else []


def _linux_connection_names() -> list[str]:
    """The names of the Wi-Fi connections NetworkManager has saved."""
    code, out = _run(["nmcli", "-t", "-f", "name,type", "connection", "show"])
    return [line.rsplit(":", 1)[0].replace("\\:", ":") for line in out.splitlines() if line.endswith(":802-11-wireless") or line.endswith(":wifi")] if code == 0 else []


def saved_profile(ssid: str) -> str | None:
    """The name of the saved profile for network `ssid` (a profile may be named differently from its SSID), or None."""
    system = platform()
    if system.startswith("win"):
        names = _windows_profile_names()
        if ssid in names:
            return ssid
        for name in names[:MAX_PROFILES_CHECKED]:
            code, out = _run(["netsh", "wlan", "show", "profile", f"name={name}"])
            found = re.search(r'^[ \t]*SSID name[ \t]*:[ \t]*"?(.*?)"?[ \t]*$', out, re.MULTILINE) if code == 0 else None
            if found and found[1] == ssid:
                return name
        return None
    if system.startswith("linux"):
        names = _linux_connection_names()
        if ssid in names:
            return ssid
        for name in names[:MAX_PROFILES_CHECKED]:
            code, out = _run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", name])
            if code == 0 and out.strip() == ssid:
                return name
    return None


def has_profile(ssid: str) -> bool:
    """Is this network already saved on this computer (so it can be joined without its password)?"""
    return saved_profile(ssid) is not None


def scan() -> list[str]:
    """Network names in range, without duplicates."""
    system = platform()
    if system.startswith("win"):
        code, out = _run(["netsh", "wlan", "show", "networks"])
        names = _NETSH_SCAN.findall(out) if code == 0 else []
    elif system.startswith("linux"):
        code, out = _run(["nmcli", "-t", "-f", "ssid", "device", "wifi", "list", "--rescan", "yes"], timeout=45)
        names = [line.replace("\\:", ":") for line in out.splitlines()] if code == 0 else []
    else:
        raise SystemcoreError("Scanning for Wi-Fi networks is not supported on this system: set SYSTEMCORE_WIFI_SSID.")
    return [name for name in dict.fromkeys(names) if name]


def pick_systemcore(names: list[str]) -> str:
    """The one SYSTEMCORE-<suffix> network in range; an error that says what to do when there are none or several."""
    found = [n for n in names if n.upper().startswith("SYSTEMCORE")]
    if len(found) == 1:
        return found[0]
    if not found:
        raise SystemcoreError("No SYSTEMCORE Wi-Fi network is in range. Check the controller is on, or set SYSTEMCORE_WIFI_SSID.")
    raise SystemcoreError(f"Several SYSTEMCORE networks are in range ({', '.join(found[:6])}). Set SYSTEMCORE_WIFI_SSID to yours.")


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _add_windows_profile(ssid: str, password: str, secret: str) -> bool:
    """Windows: save a profile named like the SSID with this password. True when it is new (False: it replaced one the user had already saved)."""
    existed = ssid in _windows_profile_names()
    with tempfile.TemporaryDirectory(prefix="systemcore-wifi-") as folder:  # private; removed on exit, whatever happens inside
        name = Path(folder) / "profile.xml"
        name.write_text(_PROFILE.format(ssid=escape(ssid), password=escape(password)), encoding="utf-8")
        code, out = _run(["netsh", "wlan", "add", "profile", f"filename={name}"])
    if code != 0:
        raise SystemcoreError("Windows would not add the Wi-Fi profile: " + _scrub(out, secret).strip()[:200])
    return not existed


def _forget_added_profile(ssid: str) -> None:
    """Remove the profile a failed join added (never one that was saved before: those are the user's)."""
    win = platform().startswith("win")
    with contextlib.suppress(SystemcoreError):
        _run(["netsh", "wlan", "delete", "profile", f"name={ssid}"] if win else ["nmcli", "connection", "delete", "id", ssid])


def join(ssid: str, password: str | None, wait_s: float = 20) -> str | None:
    """Join `ssid` and wait until this computer reports it. Returns the network that was left (if any).

    Without a password the network must already be saved on this computer (see has_profile).
    """
    system = platform()
    previous = current_ssid()
    secret = password or ""
    added = False
    if not system.startswith(("win", "linux")):
        raise SystemcoreError(f"Joining a network is not supported on this system. Join {ssid} yourself (its password is in the server's settings).")
    try:
        if system.startswith("win"):
            if password is not None:
                added = _add_windows_profile(ssid, password, secret)
            profile_name = ssid if password is not None else (saved_profile(ssid) or ssid)  # a profile we just added is named like the SSID
            code, out = _run(["netsh", "wlan", "connect", f"name={profile_name}", f"ssid={ssid}"])
        elif password is None:
            code, out = _run(["nmcli", "device", "wifi", "connect", ssid], timeout=60)
        else:
            added = ssid not in _linux_connection_names()  # nmcli saves a new connection, named like the SSID, that holds the password
            code, out = _run(["nmcli", "--ask", "device", "wifi", "connect", ssid], stdin=password + "\n", timeout=60)
        if code != 0:
            raise SystemcoreError(f"Could not join {ssid}: " + _scrub(out, secret).strip()[:200])
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if current_ssid() == ssid:
                return previous if previous != ssid else None
            time.sleep(1)
        raise SystemcoreError(f"Asked to join {ssid}, but this computer is not on it after {wait_s:g} s (wrong password, or out of range).")
    except BaseException:  # a failed join (or a kill with Ctrl+C) must not leave the profile with the password behind
        if added:
            _forget_added_profile(ssid)
        raise


def leave(ssid: str, wait_s: float = 10) -> None:
    """Disconnect from `ssid` and wait until this computer is no longer on it. The network stays saved."""
    system = platform()
    if system.startswith("win"):
        if current_ssid() != ssid:  # `netsh wlan disconnect` drops whatever network this computer is on: never another one
            return
        code, out = _run(["netsh", "wlan", "disconnect"])
    elif system.startswith("linux"):
        code, out = _run(["nmcli", "connection", "down", "id", ssid], timeout=30)
    else:
        raise SystemcoreError(f"Leaving a network is not supported on this system. Disconnect from {ssid} yourself.")
    if code != 0:
        raise SystemcoreError(f"Could not leave {ssid}: " + out.strip()[:200])
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if current_ssid() != ssid:
            return
        time.sleep(1)
    raise SystemcoreError(f"Asked to leave {ssid}, but this computer is still on it after {wait_s:g} s.")

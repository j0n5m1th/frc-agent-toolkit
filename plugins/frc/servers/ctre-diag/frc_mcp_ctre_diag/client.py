"""Talking to the Phoenix Diagnostic Server: one HTTP GET per action, a JSON answer, and an error code inside it.
Seen on a Systemcore (image alpha14, diagnostic server `2026.70.0`, port 1250): `GET /?action=<name>&k=v` answers HTTP 200 with JSON
that always includes `"GeneralReturn": {"Action", "CANbus", "Error", "ErrorMessage", "ID", "Model"}`; `Error` 0 means success and a
failure is still HTTP 200. A device is addressed by the exact `model` string, the `id` and the `canbus` that `getdevices` reports
(a partial model gives -120, "not found").
ONLY the actions in `ALLOWED_ACTIONS` are ever sent. The server also accepts actions that change devices (set a config, an id or a
name, restore factory defaults, field-upgrade firmware, activate a license, run a caniv command, deploy the diagnostic server
itself); they are deliberately not here.
"""

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from frc_mcp_ctre.errors import CtreError
from frc_mcp_systemcore_common.hosts import OPENER, redirect_message, url_host

from . import config

# The only actions ever sent: seven reads, and `blink`, which makes a device's LED blink and changes nothing that lasts.
ALLOWED_ACTIONS = frozenset({"getversion", "getdevices", "getcanbusstats", "deviceinformation", "getsignals", "getsignalvalue", "getconfigv2", "blink"})
MAX_BODY = 4 * 1024 * 1024

# What an error code means, for the ones seen (the server's own text follows; the hint says what to do about it).
HINTS = {
    -120: "Check the device is powered, on this CAN bus, and that its name, model and id are exactly as diag_status lists them.",
    -135: "This device model does not offer that.",
    -144: "CTRE reserves that feature for Tuner X.",
    -1002: "An argument was missing or malformed.",
    -10018: "No such CAN bus. Systemcore buses are can_s0 to can_s4; a CANivore is its name or serial number.",  # seen for can5 and can_s5
}


def _request_sync(host: str, action: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    if action not in ALLOWED_ACTIONS:
        raise CtreError(f"{action!r} is not an action this server sends.")
    query = urllib.parse.urlencode({"action": action, **{k: v for k, v in params.items() if v is not None}}, quote_via=urllib.parse.quote)
    url = f"http://{url_host(config.address(host))}:{config.port()}/?{query}"  # the checked address, resolved now: never the name again
    try:
        with OPENER.open(urllib.request.Request(url), timeout=timeout) as response:
            raw = response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise CtreError(f"The diagnostic server at {host} {redirect_message(e.code)}") from e
        raise CtreError(f"The diagnostic server at {host} answered HTTP {e.code} to {action}.") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise CtreError(f"No Phoenix diagnostic server answered at {host}:{config.port()} ({getattr(e, 'reason', e)}). Is the controller powered and on this "
                        "network (Systemcore Wi-Fi: 172.30.0.1)? Set DIAG_HOST or pass `host`.") from e
    if len(raw) > MAX_BODY:
        raise CtreError(f"The {action} answer is larger than {MAX_BODY // 2**20} MB; not read.")
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as e:
        raise CtreError(f"The diagnostic server's answer to {action} is not JSON: {raw[:120]!r}.") from e
    if not isinstance(data, dict) or not isinstance(data.get("GeneralReturn"), dict):
        raise CtreError(f"The diagnostic server's answer to {action} has no GeneralReturn: this is not a Phoenix diagnostic server, or a version this server does not know.")
    problem = data["GeneralReturn"]
    if problem.get("Error") != 0:
        code = problem.get("Error")
        hint = HINTS.get(code, "") if isinstance(code, int) else ""
        raise CtreError(f"Phoenix diagnostic server, {action}: {str(problem.get('ErrorMessage', 'error'))[:200]} (error {code}). {hint}".strip())
    return data


async def target(host: str | None) -> str:
    """The controller address to use (checked to be a robot-network address); resolve once per tool call and pass it to `call`."""
    return await asyncio.to_thread(config.host, host)


async def call(action: str, host: str, **params: Any) -> dict[str, Any]:
    """One action against `host`, which must come from `target` (checked once there, not per request). Raises CtreError for an unreachable server or an error code in the answer."""
    return await asyncio.to_thread(_request_sync, host, action, params, config.timeout_s())

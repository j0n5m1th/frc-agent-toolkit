"""Which hosts may be contacted, and the plain HTTP calls to the controller's services (no login needed).
Only private, loopback and link-local addresses are contacted unless SYSTEMCORE_ALLOW_ANY_HOST=1: a tool argument or text read
from the device must not be able to point this server at the open internet.
"""

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from frc_mcp_systemcore_common import hosts

from . import config
from .errors import SystemcoreError

# The controller's service ports (Seen on a limelightosr-2027.0.0-alpha14 image). Tests point these at a fake device.
PORTS = {"diag": 4800, "hwmon": 4802, "update": 4804, "bundle": 4808, "discovery": 5808, "ssh": 22, "nt": 5810}
MAX_BODY = 8 * 1024 * 1024  # a JSON reply bigger than this is not read


def port(service: str) -> int:
    """The port of a controller service: SYSTEMCORE_PORTS first, else the controller's own."""
    return config.ports().get(service, PORTS[service])


def check_host_sync(host: str) -> str:
    """`host` if it is a plain address or name that resolves to a robot-network address; else SystemcoreError."""
    return hosts.check_host(host, allow_any=config.allow_any_host(), setting="SYSTEMCORE_ALLOW_ANY_HOST", error=SystemcoreError,
                            resolve_hint="Try the IP address, or run systemcore_discover.")


def connect_address(host: str) -> str:
    """The IP address to connect to for `host`, checked now (every address of a name must be a robot-network one). Connect to this, not to the name."""
    return hosts.checked_address(host, allow_any=config.allow_any_host(), setting="SYSTEMCORE_ALLOW_ANY_HOST", error=SystemcoreError,
                                 resolve_hint="Try the IP address, or run systemcore_discover.")


async def check_host(host: str) -> str:
    return await asyncio.to_thread(check_host_sync, host)


def _unreachable(host: str, service: str, reason: object) -> SystemcoreError:
    try:
        where = str(port(service))
    except SystemcoreError:  # a bad SYSTEMCORE_PORTS must not hide the real failure
        where = "?"
    return SystemcoreError(
        f"The {service} service at {host}:{where} did not answer ({reason}). Is the Systemcore powered and on "
        "this network? systemcore_discover finds it. (A wedged hwmon service recovers with `sudo systemctl restart limelight_hwmon`.)")


def _request_sync(host: str, service: str, path: str, method: str, body: Any, timeout: float) -> Any:
    data = None if body is None else json.dumps(body).encode()
    address = hosts.url_host(connect_address(host))
    request = urllib.request.Request(f"http://{address}:{port(service)}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    try:
        with hosts.OPENER.open(request, timeout=timeout) as response:
            raw = response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise SystemcoreError(f"{service} {method} {path} {hosts.redirect_message(e.code)}") from e
        detail = e.read(300).decode("utf-8", "replace").strip()
        hint = " The Systemcore disk is full." if e.code == 507 else ""
        raise SystemcoreError(f"{service} {method} {path} returned {e.code}: {detail}{hint}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise _unreachable(host, service, getattr(e, "reason", e)) from e
    if len(raw) > MAX_BODY:
        raise SystemcoreError(f"{service} {path} reply is larger than {MAX_BODY // 1024 // 1024} MB; not read.")
    try:
        return json.loads(raw)
    except ValueError as e:
        raise SystemcoreError(f"{service} {path} did not return JSON.") from e


async def os_info(host: str, timeout: float | None = None) -> dict[str, Any]:
    """The controller's OS `release`, `hash` and `partition` (the update manager's /api/oshash; no login)."""
    return await request_json(host, "update", "/api/oshash", timeout=timeout)


async def request_json(host: str, service: str, path: str, *, method: str = "GET", body: Any = None, timeout: float | None = None) -> Any:
    """One JSON request to a controller service. Callers keep each service to one request at a time (a busy hwmon stalls)."""
    return await asyncio.to_thread(_request_sync, host, service, path, method, body, timeout or config.timeout_s())


def copy_stream(source: Any, out: Any, limit: int | None = None) -> int:
    """Copy a response body to an open file in chunks; returns the bytes in `out` afterwards. Over `limit` bytes is a SystemcoreError."""
    while chunk := source.read(256 * 1024):
        out.write(chunk)
        if limit is not None and out.tell() > limit:
            raise SystemcoreError(f"The download is larger than the {limit} byte limit; stopped.")
    return out.tell()


def _download_sync(host: str, service: str, path: str, dest: Path, timeout: float) -> int:
    request = urllib.request.Request(f"http://{hosts.url_host(connect_address(host))}:{port(service)}{path}")
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with hosts.OPENER.open(request, timeout=timeout) as response, part.open("wb") as out:
            total = copy_stream(response, out, config.max_download_bytes())
    except SystemcoreError:  # over the size limit
        part.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as e:
        part.unlink(missing_ok=True)
        if 300 <= e.code < 400:
            raise SystemcoreError(f"{service} download {hosts.redirect_message(e.code)}") from e
        raise SystemcoreError(f"{service} download returned {e.code}: {e.read(200).decode('utf-8', 'replace').strip()}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        part.unlink(missing_ok=True)
        raise _unreachable(host, service, getattr(e, "reason", e)) from e
    part.replace(dest)
    return total


async def download(host: str, service: str, path: str, dest: Path, timeout: float = 60) -> int:
    """Stream one GET to `dest` (a .part file until complete); returns the bytes saved."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    return await asyncio.to_thread(_download_sync, host, service, path, dest, timeout)

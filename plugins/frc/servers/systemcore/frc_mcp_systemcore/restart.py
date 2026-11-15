"""Restarting robot code and rebooting the Systemcore, over SSH with the managed key, using `sudo -n` (passwordless sudo;
`-n` fails instead of asking). Neither touches TCP port 1740, which is how the Driver Station does it: that port has one slot
and a connection to it replaces the Driver Station's. Both refuse while the robot is enabled or attached to a field (cannot be
forced), and when the robot's state cannot be read, unless the robot service is stopped or the caller passes force=True.
"""

import asyncio
import time
from typing import Any

from . import net, robot_state, ssh
from .errors import SystemcoreError

SERVICE = "robot"  # the robot program's systemd unit
RESTART = f"sudo -n systemctl restart {SERVICE}"
SHOW = f"systemctl show {SERVICE} -p ActiveState,SubState,MainPID,NRestarts"
# The reboot starts two seconds later in the background, so the command returns before the connection is torn down.
REBOOT = "sudo -n sh -c '(sleep 2; systemctl reboot) >/dev/null 2>&1 &'"
POLL_S = 1.0
SETTLE_S = 3.0  # after a reboot starts, an answer in the first seconds is still the old system


def parse_show(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


async def _robot_service_is_down(host: str) -> bool:
    """True only when systemd says the robot service is `inactive` or `failed`. Any trouble asking counts as not down."""
    try:
        async with ssh.session(host) as session:
            _, text, _ = await asyncio.to_thread(session.run, SHOW, 15)
    except SystemcoreError:
        return False
    return parse_show(text).get("ActiveState") in ("inactive", "failed")


async def guard(host: str, force: bool = False) -> dict[str, Any]:
    """Raise if the robot is enabled or on a field, or if its state is unknown and nothing says it is safe; otherwise the robot state to report.

    `force` only covers the unknown case. Enabled and field are never bypassed.
    """
    word = await robot_state.control_word(host)
    if blocked := robot_state.blockers(word):
        raise SystemcoreError(f"The robot is {' and '.join('enabled' if b == 'enabled' else 'attached to a field (FMS)' for b in blocked)}, so nothing was "
                              "restarted. Disable the robot first (and never do this during a match).")
    if word is not None:
        return {"robot_state": {k: word[k] for k in ("enabled", "eStop", "dsAttached") if k in word}}
    if await _robot_service_is_down(host):
        return {"robot_state": "unknown (robot program is stopped)"}
    if force:
        return {"robot_state": "unknown (forced)"}
    raise SystemcoreError("Could not tell if the robot is enabled (its state could not be read), so nothing was restarted. A restart while the robot is "
                          "enabled can be dangerous. Disable the robot and check that the robot program is running, then try again. If you are sure "
                          "the robot is disabled, call this again with force=True.")


def _sudo_failed(code: int, err: str) -> SystemcoreError:
    why = "sudo needs a password here" if "password" in err.lower() else err.strip()[:200] or f"exit {code}"
    return SystemcoreError(f"The Systemcore refused the command ({why}).")


async def restart_robot_code(host: str, wait_s: float, force: bool = False) -> dict[str, Any]:
    """Restart the robot program's service and wait until a new process is running."""
    out = await guard(host, force)
    async with ssh.session(host) as session:
        _, text, _ = await asyncio.to_thread(session.run, SHOW, 15)
        before = parse_show(text)
        started = time.monotonic()
        code, _, err = await asyncio.to_thread(session.run, RESTART, 30)
        if code != 0:
            raise _sudo_failed(code, err)
        after: dict[str, str] = {}
        while time.monotonic() - started < wait_s:
            _, text, _ = await asyncio.to_thread(session.run, SHOW, 15)
            after = parse_show(text)
            if after.get("ActiveState") == "active" and after.get("MainPID") not in (None, "0", before.get("MainPID")):
                break
            await asyncio.sleep(POLL_S)
    running = after.get("ActiveState") == "active" and after.get("MainPID") not in (None, "0", before.get("MainPID"))
    out.update(restarted=running, seconds=round(time.monotonic() - started, 1), robot_service={"state": after.get("ActiveState"), "sub": after.get("SubState"),
                                                                                                 "restarts": after.get("NRestarts")})
    if not running:
        out["hint"] = "The service did not come back with a new process in time. Check systemcore_journal (service=robot, priority=err)."
    return out


async def _uptime(host: str, timeout: float) -> float | None:
    try:
        return float((await net.request_json(host, "hwmon", "/api/time", timeout=timeout)).get("uptime_sec"))
    except (SystemcoreError, TypeError, ValueError):
        return None


async def reboot(host: str, wait_s: float, force: bool = False) -> dict[str, Any]:
    """Reboot the Systemcore and wait until it answers again with a smaller uptime."""
    out = await guard(host, force)
    before = await _uptime(host, 3)
    async with ssh.session(host) as session:
        code, _, err = await asyncio.to_thread(session.run, REBOOT, 15)
    if code != 0:
        raise _sudo_failed(code, err)
    started = time.monotonic()
    came_back = False
    while time.monotonic() - started < wait_s:
        await asyncio.sleep(2 * POLL_S)
        now = await _uptime(host, 2)
        if now is not None and (before is None or now < before) and time.monotonic() - started >= SETTLE_S:
            came_back = True
            break
    out.update(rebooting=True, came_back=came_back, seconds=round(time.monotonic() - started, 1))
    if not came_back:
        out["hint"] = ("It did not answer again in time. It may still be starting, or this computer may have lost its Wi-Fi to the Systemcore "
                       "(rejoin it, or run systemcore_connect), then run systemcore_info.")
    return out

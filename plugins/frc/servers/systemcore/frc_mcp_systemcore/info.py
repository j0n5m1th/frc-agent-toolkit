"""The Systemcore health summary: plain HTTP readings first, plus a batch of shell readings when the SSH key works.
Never called: hwmon's /api/disk/largest*, /api/logs/journal-stats (they scan the disk and can stall hwmon, one thread, for many
minutes) and /api/wifi (it returns the access point's passphrase).
"""

import asyncio
import contextlib
import time
from collections.abc import Awaitable
from typing import Any

from . import net, releases, ssh
from .errors import SystemcoreError

DISK_WARN_PCT = 85.0
CPU_TEMP_WARN_C = 80.0
RP1_TEMP_WARN_C = 85.0
CLOCK_SKEW_WARN_S = 120.0
EMMC_LIFE_WARN = 8  # the eMMC lifetime estimate counts in steps of 10%: 1 = 0-10% used, 11 = over 100%

SHELL_SUMMARY = r"""
echo '##mem'; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo
echo '##load'; cat /proc/loadavg
echo '##temp'
for z in /sys/class/thermal/thermal_zone*; do echo "thermal $(cat $z/type 2>/dev/null) $(cat $z/temp 2>/dev/null)"; done
for h in /sys/class/hwmon/hwmon*; do n=$(cat $h/name 2>/dev/null); echo "hwmon $n $(cat $h/temp1_input 2>/dev/null) $(cat $h/in0_lcrit_alarm 2>/dev/null)"; done
echo '##failed'; systemctl --failed --no-legend 2>/dev/null | awk '{print $1}'
echo '##robot'; systemctl show robot -p ActiveState,SubState,NRestarts 2>/dev/null
echo '##errors'; journalctl -b -p err --no-pager -q 2>/dev/null | wc -l
"""


def parse_shell_summary(text: str) -> dict[str, Any]:
    """Numbers from the SHELL_SUMMARY output; a section that is missing or malformed is left out."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("##"):
            current = line[2:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line.strip())
    out: dict[str, Any] = {}
    mem = {}
    for line in sections.get("mem", []):
        parts = line.replace(":", "").split()
        if len(parts) >= 2 and parts[1].isdigit():
            mem[parts[0]] = int(parts[1]) // 1024
    if "MemTotal" in mem and "MemAvailable" in mem:
        out["memory_mb"] = {"total": mem["MemTotal"], "available": mem["MemAvailable"]}
    if (load := (sections.get("load") or [""])[0].split()[:3]) and len(load) == 3:
        with contextlib.suppress(ValueError):
            out["load"] = [float(v) for v in load]
    temps: dict[str, float] = {}
    undervoltage: bool | None = None
    for line in sections.get("temp", []):
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "thermal" and parts[1] == "cpu-thermal" and parts[2].lstrip("-").isdigit():
            temps["cpu_c"] = int(parts[2]) / 1000
        elif len(parts) >= 3 and parts[0] == "hwmon" and parts[1] == "rp1_adc" and parts[2].lstrip("-").isdigit():
            temps["rp1_c"] = int(parts[2]) / 1000
        elif len(parts) >= 3 and parts[0] == "hwmon" and parts[1] == "rpi_volt" and parts[-1] in ("0", "1"):
            undervoltage = parts[-1] == "1"
    if temps:
        out["temps"] = temps
    if undervoltage is not None:
        out["undervoltage"] = undervoltage
    if "failed" in sections:
        out["failed_units"] = [u for u in sections["failed"] if u]
    robot = dict(line.split("=", 1) for line in sections.get("robot", []) if "=" in line)
    if robot:
        out["robot_service"] = {"state": robot.get("ActiveState"), "sub": robot.get("SubState"),
                                "restarts": int(robot["NRestarts"]) if robot.get("NRestarts", "").isdigit() else None}
    if (errors := (sections.get("errors") or [""])[0]).isdigit():
        out["error_lines_this_boot"] = int(errors)
    return out


def warnings_for(info: dict[str, Any]) -> list[str]:
    """The things that need attention, in plain words."""
    found: list[str] = []
    if newer := info.get("os_update"):
        found.append(f"A newer OS release is available: {newer['tag']} (this one is {info['os'].get('release')}).")
    if (disk := info.get("disk")) and disk.get("used_pct", 0) >= DISK_WARN_PCT:
        found.append(f"Disk is {disk['used_pct']}% full; prune logs.")
    if (emmc := info.get("emmc")) and (emmc.get("status") != "good" or emmc.get("life_worst", 0) >= EMMC_LIFE_WARN):
        found.append(f"eMMC health is {emmc.get('status')} (lifetime estimate {emmc.get('life_worst')}/11).")
    clock = info.get("clock") or {}
    if clock.get("skew_s") is not None and abs(clock["skew_s"]) > CLOCK_SKEW_WARN_S:
        found.append(f"The Systemcore clock is {abs(clock['skew_s']) / 3600:.1f} h {'behind' if clock['skew_s'] < 0 else 'ahead'}: "
                     "file dates and TBD log names are unreliable.")
    temps = info.get("temps") or {}
    if temps.get("cpu_c", 0) >= CPU_TEMP_WARN_C:
        found.append(f"CPU is {temps['cpu_c']:.0f} C (the OS powers off at a sustained 95 C).")
    if temps.get("rp1_c", 0) >= RP1_TEMP_WARN_C:
        found.append(f"RP1 sensor is {temps['rp1_c']:.0f} C.")
    if info.get("undervoltage"):
        found.append("Undervoltage alarm is set: check the power supply.")
    if info.get("failed_units"):
        found.append("Failed services: " + ", ".join(info["failed_units"][:8]))
    if (memory := info.get("memory_mb")) and memory["available"] < memory["total"] * 0.1:
        found.append(f"Only {memory['available']} MB of memory is available.")
    if (robot := info.get("robot_service")) and robot.get("state") not in (None, "active"):
        found.append(f"robot.service is {robot['state']}.")
    return found


async def _try(name: str, coro: Awaitable[Any], errors: dict[str, str]) -> Any:
    try:
        return await coro
    except SystemcoreError as e:
        errors[name] = str(e)
        return None


async def _diag(host: str, errors: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if (oshash := await _try("oshash", net.os_info(host), errors)) is not None:
        release = oshash.get("release")
        out["os"] = {"release": release, "hash": oshash.get("hash"), "slot": oshash.get("partition"), **releases.parse_release(release)}
    if (team := await _try("team", net.request_json(host, "diag", "/api/team"), errors)) is not None:
        out["team"] = team.get("team_number")
    return out


async def _hwmon(host: str, errors: dict[str, str]) -> dict[str, Any]:
    """hwmon is one thread: its calls go one after another."""
    out: dict[str, Any] = {}
    if (time_info := await _try("time", net.request_json(host, "hwmon", "/api/time"), errors)) is not None:
        skew = (time_info["epoch_ms"] / 1000 - time.time()) if isinstance(time_info.get("epoch_ms"), (int, float)) else None
        out["clock"] = {"ntp_synced": time_info.get("ntp_synced"), "timezone": time_info.get("timezone"),
                        "skew_s": None if skew is None else round(skew)}
        out["uptime_s"] = round(time_info.get("uptime_sec", 0))
    if (cpu := await _try("cpu", net.request_json(host, "hwmon", "/api/cpu/slices"), errors)) is not None:
        out["cpu"] = {"cores": cpu.get("ncpu"), "slices": {s["slice"]: s["cpu_pct"] for s in cpu.get("slices", []) if "slice" in s}}
    if (disks := await _try("disk", net.request_json(host, "hwmon", "/api/disk/info"), errors)) is not None:
        root = next((d for d in disks.get("disks", []) if d.get("mountpoint") == "/"), None)
        if root:
            out["disk"] = {"total_gb": round(root["total_bytes"] / 1e9, 1), "free_gb": round(root["available_bytes"] / 1e9, 1),
                           "used_pct": round(root["utilization_percent"], 1)}
    if (emmc := await _try("emmc", net.request_json(host, "hwmon", "/api/disk/emmchealth"), errors)) is not None:
        out["emmc"] = {"status": emmc.get("status"), "life_worst": emmc.get("lifeTimeWorst"), "pre_eol": emmc.get("preEol")}
    return out


async def _shell(host: str, os_hash: str | None, errors: dict[str, str]) -> dict[str, Any]:
    try:
        async with ssh.session(host, os_hash) as session:
            _, text, _ = await asyncio.to_thread(session.run, SHELL_SUMMARY, 30)
    except SystemcoreError as e:
        errors["ssh"] = str(e)
        return {}
    return parse_shell_summary(text)


async def summary(host: str) -> dict[str, Any]:
    """Everything the summary covers, `warnings` first, and `errors` for each source that did not answer."""
    errors: dict[str, str] = {}
    diag, hwmon = await asyncio.gather(_diag(host, errors), _hwmon(host, errors))
    shell = await _shell(host, (diag.get("os") or {}).get("hash"), errors)
    info = {**diag, **hwmon, **shell}
    saved = releases.cached()  # the saved list only: there is no internet here
    if (newest := releases.newest_for(info.get("os") or {}, saved.get("releases", []))) and releases.is_newer(newest["tag"], info["os"]):
        info["os_update"] = {"tag": newest["tag"], "checked_at": saved.get("fetched_at")}
    out: dict[str, Any] = {"host": host, "warnings": warnings_for(info), **info}
    out.pop("os_update", None)  # the warning already says it
    if os_info := out.get("os"):  # the release name carries version, stage and image; a short hash is enough to tell images apart
        out["os"] = {"release": os_info.get("release"), "slot": os_info.get("slot"), "hash": str(os_info.get("hash") or "")[:8]}
    if errors:
        out["errors"] = errors
    return out


async def detail(host: str, area: str) -> dict[str, Any]:
    """One area in more depth (network, hardware, services, dmesg)."""
    errors: dict[str, str] = {}
    out: dict[str, Any] = {"host": host, "area": area}
    if area == "network":
        addresses = await _try("addresses", net.request_json(host, "hwmon", "/api/network/addresses"), errors)
        config_ = await _try("network", net.request_json(host, "diag", "/api/network"), errors)
        out["hostname"] = (addresses or {}).get("hostname")
        out["interfaces"] = [{"name": a["ifn"], "ip": a["ip"], "up": a["up"]} for a in (addresses or {}).get("addresses", [])]
        # Settings only: the Wi-Fi access point's passphrase lives in /api/wifi, which is never read.
        out["configured"] = {name: {k: v for k, v in item.items() if k in ("static_ip", "use_dhcp", "gateway", "netmask", "enabled")}
                             for name, item in ((config_ or {}).get("interfaces") or {}).items()}
    elif area == "hardware":
        usb = await _try("usb", net.request_json(host, "hwmon", "/api/usb"), errors)
        i2c = await _try("i2c", net.request_json(host, "hwmon", "/api/i2c"), errors)
        out["usb"] = [f"{d.get('vendor_id')}:{d.get('product_id')} {d.get('description')}" for d in (usb or {}).get("devices", [])]
        out["i2c"] = [{"bus": b["bus_number"], "devices": b["device_count"]} for b in (i2c or {}).get("buses", [])]
    else:
        command = {"services": "systemctl list-units --type=service --state=running --no-legend | awk '{print $1}'",
                   "dmesg": "dmesg | tail -n 60"}[area]
        try:
            async with ssh.session(host) as session:
                _, text, _ = await asyncio.to_thread(session.run, command, 20)
            lines = [line[:300] for line in text.splitlines() if line.strip()]
            out["running" if area == "services" else "lines"] = lines
        except SystemcoreError as e:
            errors["ssh"] = str(e)
    if errors:
        out["errors"] = errors
    return out

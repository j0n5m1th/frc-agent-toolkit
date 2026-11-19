"""`nt4_health`'s judgement: a Systemcore's own health topics (port 6810) in, ranked `flags` and the numbers behind them out.

Pure functions over already-read values, so they are tested without a network. The topic names are the ones the `systemcore-live` skill
lists (seen on a real Systemcore, image alpha18). The limits are rules of thumb, not WPILib or vendor figures.
"""

from typing import Any

SYS, CANBUS, DIAGNOSTICS, STATUS, ALERTS = "/sys/", "/canbus/", "/diagnostics/", "/Netcomm/Status/", "/Netcomm/Alerts/"
PREFIXES = (SYS, CANBUS, DIAGNOSTICS, STATUS, ALERTS)
MAX_TOPICS = 150  # read at most this many topics in one call
MAX_ALERTS = 5
BROWNOUT_VOLTS, LOW_VOLTS = 7.0, 11.5  # below the first the roboRIO-class controllers brown out; the second is a low resting battery
BUSY_PCT = 90.0  # CPU, memory and storage use above this
CAN_BUSY_PCT = 80.0
LEVELS = {0: "high", 1: "med", 2: "low"}  # an alert's level is the second-to-last path segment: 0 high, 1 medium, 2 low
SEVERITY = {"high": 0, "med": 1, "low": 2}
NOT_HERE = ("no /sys topics: this is not the Systemcore's own server. Connect to it with nt4_connection port=6810 "
            "(host: the controller's address); the simulator has none of these")


def is_on(value: Any) -> bool:
    """A flag or counter as a yes/no: nonzero numbers and true booleans; text counts unless it reads as empty, 0 or false."""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "none", "ok")
    return bool(value)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _alert_parts(name: str) -> tuple[str, int | None]:
    """(path of the alert, its level) from `/Netcomm/Alerts/<group>/<level>/<id>/active`."""
    base = name.removesuffix("/active")
    parts = base.removeprefix(ALERTS).split("/")
    level = int(parts[-2]) if len(parts) >= 2 and parts[-2].isdigit() else None
    return base, level


def active_alerts(values: dict[str, Any]) -> list[str]:
    """The base paths of the alerts that are on, most severe first."""
    on = [_alert_parts(n) for n, v in values.items() if n.startswith(ALERTS) and n.endswith("/active") and is_on(v)]
    return [base for base, level in sorted(on, key=lambda a: (9 if a[1] is None else a[1], a[0]))][:MAX_ALERTS]


def assess(values: dict[str, Any], texts: dict[str, str]) -> dict[str, Any]:
    """`values`: topic -> its current value (topics with none left out). `texts`: active alert base path -> its text."""
    if not any(name.startswith(SYS) for name in values):
        return {"flags": [f"low: controller health not checked: {NOT_HERE}"], "controller": {"checked": False, "why": NOT_HERE}}
    flags: list[str] = []
    controller: dict[str, Any] = {}

    def num(name: str) -> float | None:
        return _number(values.get(name))

    volts = num(SYS + "battery")
    if volts is not None:
        controller["battery_v"] = round(volts, 2)
        if volts < BROWNOUT_VOLTS:
            flags.append(f"high: battery reads {volts:.1f} V (below {BROWNOUT_VOLTS:g} V the controller can brown out)")
        elif volts < LOW_VOLTS:
            flags.append(f"med: battery reads {volts:.1f} V (low; a resting battery should be higher, but a load pulls it down)")
    if (SYS + "brownout") in values:
        controller["brownout"] = is_on(values[SYS + "brownout"])
        if controller["brownout"]:
            flags.append("high: the controller reports a brownout")
    for label, name in (("cpu_pct", "cpu"), ("ram_pct", "ramutil"), ("storage_pct", "storageutil")):
        if (use := num(SYS + name)) is not None:
            controller[label] = round(use, 1)
            if use > BUSY_PCT:
                flags.append(f"med: {label} is {use:.0f}")
    if (temp := num(SYS + "temp")) is not None:
        controller["temp"] = round(temp, 1)  # the unit is not published, so it is shown but never judged
    if (SYS + "emmc/status") in values:
        controller["emmc"] = values[SYS + "emmc/status"]
    down = {n.removeprefix(SYS + "faults/"): v for n, v in values.items() if n in (SYS + "faults/canbus_down", SYS + "faults/canbus_unavail") and is_on(v)}
    if down:
        controller["can_faults"] = down
        flags.append(f"high: CAN fault flag(s) set: {', '.join(down)}")
    counts = {n.removeprefix(SYS + "faultcounts/"): v for n, v in values.items() if n.startswith(SYS + "faultcounts/") and is_on(v)}
    if counts:
        controller["fault_counts_since_boot"] = counts
        flags.append(f"low: {len(counts)} fault counter(s) above zero since boot (history, not live): {', '.join(list(counts)[:3])}")
    buses: dict[str, dict[str, Any]] = {}
    for name, value in values.items():
        if name.startswith(CANBUS) and name.count("/") == 3:
            bus, field = name.removeprefix(CANBUS).split("/")
            buses.setdefault(bus, {})[field] = value
    if buses:
        controller["can_buses"] = {bus: {k: v for k, v in fields.items() if k in ("name", "up", "error")} for bus, fields in sorted(buses.items())}
        for bus, fields in sorted(buses.items()):
            if is_on(fields.get("error")):
                flags.append(f"med: CAN bus {bus} reports an error")
    util = values.get(DIAGNOSTICS + "canbusutil")
    if isinstance(util, (list, tuple)) and util:
        # seen on a real Systemcore (image alpha18): two numbers per bus, the percent first and the same use as a fraction second
        per_bus = [round(float(u), 1) for u in util[0::2]]
        controller["can_util_pct"] = per_bus
        if max(per_bus) > CAN_BUSY_PCT:
            flags.append(f"med: CAN bus use is {max(per_bus):.0f}% on bus s{per_bus.index(max(per_bus))}")
    if (health := values.get(DIAGNOSTICS + "canbushealth")) is not None:
        controller["can_health"] = health  # meaning not documented here, so shown as read
    code = {key: is_on(values[STATUS + name]) for key, name in (("running", "HasUserCode"), ("ready", "HasUserCodeReady")) if STATUS + name in values}
    if code:
        controller["robot_code"] = code
        if code.get("running") is False:
            flags.append("high: no robot code is running")
        elif code.get("ready") is False:
            flags.append("med: robot code is running but not ready")
    alerts = []
    for base in active_alerts(values):
        number = _alert_parts(base)[1]
        level = LEVELS.get(number, "low") if number is not None else "low"  # level 0 is high, so test for None, not falsy
        text = texts.get(base) or base.removeprefix(ALERTS)
        alerts.append({"level": level, "text": text[:120]})
        flags.append(f"{level}: alert: {text[:100]}")
    out: dict[str, Any] = {"controller": controller}
    if alerts:
        out["alerts"] = alerts
    out["flags"] = sorted(flags, key=lambda f: SEVERITY[f.split(":", 1)[0]]) or ["none found by these checks"]
    out["not_checked"] = "the robot program (port 5810), motors and mechanisms, and any value this controller does not publish"
    return out

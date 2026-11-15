"""Functional test of `mcp-ctre-diag` (the Phoenix Diagnostic Server reader): the installed command, over stdio.

By default it needs no robot, no network and no CTRE program: `evaluations/fake_diag.py` is a real HTTP server on localhost that answers the way the real diagnostic server
was seen to (shapes, error codes, an error inside an HTTP 200), and the test proves what the server sends, what it refuses and how it shapes the answers. With `--host <address>`
it instead runs READ-ONLY checks against a real controller (for example a Systemcore at 172.30.0.1): no blink, no change of any kind is sent.
"""

from fake_diag import FakeDiag, Server
from harness import Report, call, client

from frc_mcp_ctre_diag.client import ALLOWED_ACTIONS

TOOLS = {"diag_status", "diag_bus", "diag_device", "diag_read", "diag_config", "diag_blink"}
ALLOWED = set(ALLOWED_ACTIONS)


async def run(keep: bool = False, host: str | None = None) -> bool:
    report = Report("ctre-diag server")
    if host:
        await real(report, host)
    else:
        fake = FakeDiag()
        with Server(fake) as server:
            await simulated(report, fake, {"DIAG_HOST": "127.0.0.1", "DIAG_PORT": str(server.port)})
    return report.summary()


async def simulated(report: Report, fake: FakeDiag, env: dict[str, str]) -> None:
    async with client("mcp-ctre-diag", env) as c:
        report.check("offers exactly the 6 documented tools", {t.name for t in await c.list_tools()} == TOOLS)

        status = await call(c, "diag_status")
        report.check("status: the server's version, platform and compliancy, and the devices as rows",
                     status.get("server", {}).get("system") == "Systemcore" and status["server"].get("compliancy") == 21 and len(status.get("devices", [])) == 3, status)

        device = await call(c, "diag_device", {"device": "0 - DT-GYRO"})
        report.check("device: identity, a license summary without license ids, and the faults set now and since power-up",
                     device.get("model") == "Pigeon 2 vers. S" and device.get("license", {}).get("pro") is True and "LIC-" not in str(device)
                     and device.get("faults") == [] and "Undervoltage" in device.get("sticky", []), device)

        values = await call(c, "diag_read", {"device": "0", "signals": ["Yaw", "SupplyVoltage"]})
        report.check("read: live values by signal name", values.get("values", {}).get("Yaw") == 189.2313 and values["values"].get("SupplyVoltage") == 13.13, values)

        listing = await call(c, "diag_read", {"device": "0", "name_contains": "fault"})
        report.check("read without names lists the matching signals", listing.get("matches") == 5, listing)

        config = await call(c, "diag_config", {"device": "0"})
        report.check("config: only what differs from the default", config.get("differ") == 2 and [r[1] for r in config.get("rows", [])] == ["Mount Pose Yaw", "Enable Compass"], config)

        bus = await call(c, "diag_bus")
        report.check("bus: counters appear only when not zero", bus.get("buses", [{}, {}])[1].get("counters", {}).get("BusErrorCnt") == 7 and "counters" not in bus["buses"][0], bus)

        ambiguous = await call(c, "diag_device", {"device": "5"})
        report.check("a name that fits two devices lists them", "matches 2 devices" in ambiguous.get("EXC", ""), ambiguous)

        refused = await call(c, "diag_status", {"host": "8.8.8.8"})
        report.check("an address outside the robot network is not contacted", "not a private address" in refused.get("EXC", ""), refused)

        blink = await call(c, "diag_blink", {"device": "0 - DT-GYRO"})
        report.check("blink is the one action that touches a device, and only blinks its LED", blink.get("blinked") is True and fake.actions().count("blink") == 1, blink)

        report.check("only read actions and the blink were ever sent to the server", bool(fake.requests) and set(fake.actions()) <= ALLOWED, sorted(set(fake.actions())))

    async with client("mcp-ctre-diag", {**env, "DIAG_TOOLSET": "core"}) as c:
        report.check("DIAG_TOOLSET=core hides diag_config and diag_blink", {t.name for t in await c.list_tools()} == TOOLS - {"diag_config", "diag_blink"})

    async with client("mcp-ctre-diag", {**env, "DIAG_PORT": "9"}) as c:
        nothing = await call(c, "diag_status")
        report.check("with nothing listening, the error says where it looked", "No Phoenix diagnostic server answered at 127.0.0.1:9" in nothing.get("EXC", ""), nothing)


async def real(report: Report, host: str) -> None:
    """Read-only checks against a real controller. Nothing is blinked, set or flashed."""
    async with client("mcp-ctre-diag", {"DIAG_HOST": host}) as c:
        report.check("offers exactly the 6 documented tools", {t.name for t in await c.list_tools()} == TOOLS)
        status = await call(c, "diag_status")
        server = status.get("server", {})
        report.check("the controller's diagnostic server answers with a version and compliancy", bool(server.get("version")) and isinstance(server.get("compliancy"), int), status)
        devices = status.get("devices", [])
        report.check("status lists the CAN devices as rows (possibly none)", status.get("cols") is not None and isinstance(devices, list), status)
        bus = await call(c, "diag_bus")
        report.check("bus statistics answer for every bus with a device", "EXC" not in bus and len(bus.get("buses", [])) >= (1 if devices else 0), bus)
        for row in devices[:3]:
            name = row[0]
            info = await call(c, "diag_device", {"device": name})
            report.check(f"device {name!r}: identity and faults can be read", "EXC" not in info and "faults" in info and "sticky" in info, info)
            signals = await call(c, "diag_read", {"device": name, "name_contains": "fault"})
            report.check(f"device {name!r}: its signals can be listed", "EXC" not in signals and signals.get("total", 0) > 0, signals)
        report.skip("blink and every other change", "a real controller is only read; nothing is sent that changes a device")

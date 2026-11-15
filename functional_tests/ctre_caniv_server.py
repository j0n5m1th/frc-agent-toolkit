"""Functional test of `mcp-ctre-caniv` (CANivore): the installed command, over stdio. Not run on macOS, where caniv does not exist.

Needs no network, no CTRE program and no CANivore. "caniv 1.1.1.0" is `fake_caniv.py` behind a tiny launcher: its list layout, info and license lines, its `-102` error with
no device and its `-206` for a bad firmware file are what the real one printed (with made-up values); only what `--name`, `--reset` and `--flash` print is its own. Every call
it receives is appended to a log, so the test can prove what ran and what did not: that nothing changes a device without the owner's switch, never for a device it does not list,
and never for `*`.
"""

from pathlib import Path

from ctre_support import HERE, case, install_fake
from harness import Report, call, client, is_unknown_tool, tool_names

from frc_mcp_ctre.platform import platform_key

ALL = {"caniv_list", "caniv_info", "caniv_blink", "caniv_firmware", "caniv_configure", "caniv_flash"}
CORE = ALL - {"caniv_configure", "caniv_flash"}  # also what is listed with both switches off: each change tool is hidden until its CANIV_ALLOW_* switch is on
GOOD = "CANivore-26.1.0.0-Season2026.crf"


def ran(log: Path, word: str) -> list[str]:
    return [line for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []) if word in line]


async def run(keep: bool = False) -> bool:
    with case("ctre-caniv server", "ctre-caniv", keep) as (report, folder, env):
        log = folder / "caniv-calls.log"
        firmware = folder / "firmware"
        for name in (GOOD, "CANivore-25.4.0.0-Season2025.crf", "TalonFXS-26.3.0.1-Phoenix6.crf"):
            (firmware / name).write_bytes(b"firmware")
        (firmware / "CANivore-1.0.0.0-Bad.crf").write_bytes(b"")
        env = {**env, "FAKE_CANIV_LOG": str(log)}
        if platform_key() == "macosuniversal":
            async with client("mcp-ctre-caniv", env) as c:
                refused = await call(c, "caniv_list")
                report.check("on macOS caniv is reported as not available, with CTRE's note", "not available on macOS" in refused.get("EXC", ""), refused)
        else:
            await run_checks(report, env, folder, log)
    return report.summary()


async def run_checks(report: Report, env: dict[str, str], folder: Path, log: Path) -> None:
    async with client("mcp-ctre-caniv", env) as c:
        report.check("offers the 4 read tools and hides the two that change a device", await tool_names(c) == CORE)
        missing = await call(c, "caniv_list")
        report.check("with no caniv installed, the error gives the install command", "ctre_install tool=caniv" in missing.get("EXC", ""), missing)

    install_fake(folder / "tools", "caniv", "1.1.1.0", HERE / "fake_caniv.py")

    async with client("mcp-ctre-caniv", env) as c:
        listed = await call(c, "caniv_list")
        report.check("lists the CANivores by the columns of caniv's header row, through a real child process",
                     listed.get("total") == 2 and listed["devices"][0] == {"serial": "0123ABCD", "name": "robot-bus", "status": "Application", "app": "26.1.0.0"}, listed)

        info = await call(c, "caniv_info", {"device": "robot-bus"})
        report.check("info returns the Application Firmware, caniv's own key: value pairs and the license lines for the named device",
                     info.get("device") == "robot-bus" and info.get("firmware") == "26.1.0.0" and info.get("info", {}).get("CAN Bus Termination") == "true" and "USB Name" not in info.get("info", {})
                     and info.get("license") == ["Number of Licenses: 1", "License 0: LIC-00-00000000-B-FRC"], info)

        blink = await call(c, "caniv_blink", {"device": "ROBOT-BUS"})
        report.check("blink resolves a name in any case to the serial caniv printed", blink.get("device") == "0123ABCD" and ran(log, "-d 0123ABCD --blink"), blink)

        typo = await call(c, "caniv_blink", {"device": "typo"})
        report.check("a device caniv does not list is refused, naming the ones it does", "It lists: 0123ABCD (robot-bus), 0456EF (spare)" in typo.get("EXC", ""), typo)
        star = await call(c, "caniv_blink", {"device": "*"})
        report.check("`*` (any device) is refused for a change", "not allowed for a change" in star.get("EXC", ""), star)
        report.check("neither refused call reached caniv with a change", len(ran(log, "--blink")) == 1)

        firmware = await call(c, "caniv_firmware")
        report.check("the firmware list is just the CANivore file names, highest version first",
                     firmware.get("files") == [GOOD, "CANivore-25.4.0.0-Season2025.crf", "CANivore-1.0.0.0-Bad.crf"], firmware)
        full = await call(c, "caniv_info", {"device": "robot-bus", "full": True})
        report.check("info full adds the keys left out by default", full.get("info", {}).get("USB Name") == "robot-bus" and "USB Serial Number" in full.get("info", {}), full)

        configure = await call(c, "caniv_configure", {"device": "robot-bus", "name": "drive-bus", "reset": True})
        flash = await call(c, "caniv_flash", {"device": "robot-bus", "file": GOOD})
        report.check("renaming, resetting and flashing are unknown tools without the owner's switches",
                     is_unknown_tool(configure, "caniv_configure") and is_unknown_tool(flash, "caniv_flash"), (configure, flash))
        report.check("and nothing was sent to caniv for them", not (ran(log, "--name") or ran(log, "--reset") or ran(log, "--flash")))

    async with client("mcp-ctre-caniv", {**env, "CANIV_TOOLSET": "core"}) as c:
        report.check("CANIV_TOOLSET=core hides the two tools that change a device", await tool_names(c) == CORE)
    async with client("mcp-ctre-caniv", {**env, "CANIV_TOOLSET": "core", "CANIV_ALLOW_CONFIG": "1", "CANIV_ALLOW_FLASH": "1"}) as c:
        report.check("core keeps them hidden even with both switches on", await tool_names(c) == CORE)

    async with client("mcp-ctre-caniv", {**env, "CANIV_ALLOW_CONFIG": "1"}) as c:
        report.check("CANIV_ALLOW_CONFIG=1 lists only caniv_configure of the two", await tool_names(c) == CORE | {"caniv_configure"})
        flash_off = await call(c, "caniv_flash", {"device": "robot-bus", "file": GOOD})
        report.check("flashing stays an unknown tool without CANIV_ALLOW_FLASH", is_unknown_tool(flash_off, "caniv_flash"), flash_off)
    async with client("mcp-ctre-caniv", {**env, "CANIV_ALLOW_FLASH": "1"}) as c:
        report.check("CANIV_ALLOW_FLASH=1 lists only caniv_flash of the two", await tool_names(c) == CORE | {"caniv_flash"})
        config_off = await call(c, "caniv_configure", {"device": "robot-bus", "reset": True})
        report.check("configuring stays an unknown tool without CANIV_ALLOW_CONFIG", is_unknown_tool(config_off, "caniv_configure"), config_off)
        report.check("and neither call reached caniv", not (ran(log, "--name") or ran(log, "--reset") or ran(log, "--flash")))

    async with client("mcp-ctre-caniv", {**env, "CANIV_ALLOW_CONFIG": "1", "CANIV_ALLOW_FLASH": "1"}) as c:
        report.check("with both switches on, all 6 tools are listed", await tool_names(c) == ALL)
        done = await call(c, "caniv_configure", {"device": "robot-bus", "name": "drive-bus", "reset": True})
        report.check("with the switch on, rename then reset run in that order against the serial",
                     done == {"device": "0123ABCD", "renamed": "drive-bus", "reset": True}
                     and [line.split()[2] for line in ran(log, "-d 0123ABCD") if "--name" in line or "--reset" in line] == ["--name", "--reset"], done)

        none = await call(c, "caniv_configure", {"device": "nope", "reset": True})
        report.check("a change for an unlisted device is still refused", "does not list" in none.get("EXC", ""), none)

        flashed = await call(c, "caniv_flash", {"device": "0456EF", "file": GOOD})
        report.check("flash runs caniv with the resolved CANivore file", flashed.get("file") == GOOD and ran(log, "-d 0456EF --flash"), flashed)

        other = await call(c, "caniv_flash", {"device": "0456EF", "file": "TalonFXS-26.3.0.1-Phoenix6.crf"})
        report.check("another device's firmware file is refused", "is not a CANivore firmware file" in other.get("EXC", ""), other)

        outside = await call(c, "caniv_flash", {"device": "0456EF", "file": str(folder / "index.json")})
        report.check("a file that is not .crf is refused", "Only .crf files" in outside.get("EXC", ""), outside)

        bad = await call(c, "caniv_flash", {"device": "0456EF", "file": "CANivore-1.0.0.0-Bad.crf"})
        report.check("a firmware file caniv cannot read is reported with its code", "CRF could not be read" in bad.get("EXC", "") and "-206" in bad.get("EXC", ""), bad)

    async with client("mcp-ctre-caniv", {**env, "FAKE_CANIV_DEVICES": ""}) as c:
        empty = await call(c, "caniv_list")
        report.check("with no device connected, list says so", empty.get("total") == 0 and "Connect one by USB" in empty.get("hint", ""), empty)
        none = await call(c, "caniv_info")
        report.check("info with no device explains caniv's error", "Could not open device" in none.get("EXC", "") and "-102" in none.get("EXC", ""), none)

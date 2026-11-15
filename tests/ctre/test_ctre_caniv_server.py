"""The CANivore server through the MCP protocol, with a stand-in for caniv that prints what the real one was seen to print (see frc_mcp_ctre_caniv/caniv.py).

The `--list` header, the tab-indented `--info` / `--license` lines and the `-102` / `-206` errors are the real ones (seen with one CANivore connected; the sample below is
that output with the serial number and license changed). What the stand-in prints for `--name`, `--reset` and `--flash` is its own: those were never run on a real device.
"""

import json

import pytest
from ctre_helpers import call, fake_install
from fastmcp import Client
from fastmcp.exceptions import ToolError

from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.runner import RunResult
from frc_mcp_ctre_caniv import caniv
from frc_mcp_ctre_caniv.app import apply_toolset
from frc_mcp_ctre_caniv.spec import SPEC

mcp, _ = SPEC.load()

HEADER = f"{'Serial Num':<20}{'Name':<20}{'Status':<20}{'Application Vers':<20}"
DEVICES = (("0123ABCD", "robot-bus", "Application", "26.1.0.0"), ("0456EF", "spare", "Application", "25.4.0.0"))
NO_DEVICE = 'Error value:-102\nError Message:"Could not open device"'

# Real output of `caniv --list`, `--info` and `--license` (one CANivore), changed only in the serial number (made up) and license; the Name column starts at 34 because caniv pads to the serial.
REAL_LIST = f"{'Serial Num':<34}{'Name':<20}{'Status':<20}{'Application Vers':<20}\r\n{'00000000000000000000000000000001':<34}{'canfd':<20}{'Application':<20}{'26.6.0':<20}\r\n"
REAL_INFO = (
    "\tUSB Serial Number:            00000000000000000000000000000001\r\n\tUSB Firmware Vers:            26.6.0\r\n\tUSB Status:                   Application\r\n"
    "\tUSB Name:                     canfd\r\n\tUSB Discovery Status:         \r\n\t\r\n\tApplication Firmware:         26.70.0.0\r\n"
    "\tBootloader Firmware:          1.6\r\n\tManufacture Date:             Jan 20, 2022\r\n\tESP32 Enable:                 false\r\n"
    "\tCAN Bus Termination:          true\r\n\t                              \r\n\tV+ Supply (V):                0.0867577\r\n\tCAN Bus Enable:               true\r\n"
)
REAL_LICENSE = "\tNumber of Licenses:           1\r\n\tLicense 0:                    LIC-00-00000000-B-FRC\r\n\t\r\n"


class FakeCaniv:
    """Stands in for caniv: lists DEVICES (or none), answers info/license/blink/name/reset/flash for a listed device, and records every call."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.devices = DEVICES
        self.list_text: str | None = None  # a layout the parser does not know, to test the fallbacks

    async def __call__(self, exe, args, timeout_s):
        self.calls.append([*args, f"timeout={timeout_s:g}"])
        if args == ["--list"]:
            if self.list_text is not None:
                return RunResult(0, self.list_text, False)
            return RunResult(0, "\n".join([HEADER, *(f"{s:<20}{n:<20}{st:<20}{v:<20}" for s, n, st, v in self.devices)]), False)
        serials = {d[0] for d in self.devices} | {d[1] for d in self.devices}
        if args[0] == "-d" and args[1] not in serials or args[0] == "--any" and not self.devices:
            return RunResult(127, NO_DEVICE, False)  # seen with no device
        if "--flash" in args and not open(args[args.index("--flash") + 1], "rb").read():  # noqa: SIM115
            return RunResult(127, 'Error value:-206\nError Message:"CRF could not be read"', False)
        action = next(a for a in args if a.startswith("--"))
        if action == "--info":
            return RunResult(0, REAL_INFO, False)
        if action == "--license":
            return RunResult(0, REAL_LICENSE, False)
        return RunResult(0, "" if action == "--blink" else f"{action[2:]} done", False)  # blink prints nothing (seen)


@pytest.fixture
def fake(monkeypatch, tools_dir, index_file):
    fake_install(tools_dir, "caniv", "caniv", "1.1.1.0")
    stand_in = FakeCaniv()
    monkeypatch.setattr(caniv, "run_tool", stand_in)
    return stand_in


@pytest.fixture
def firmware(tmp_path, monkeypatch):
    folder = tmp_path / "fw"
    folder.mkdir()
    for name in ("CANivore-26.1.0.0-Season2026.crf", "CANivore-25.4.0.0-Season2025.crf", "CANivore-Application-23.2.4.0-Season2023.crf", "TalonFXS-26.3.0.1-Phoenix6.crf"):
        (folder / name).write_bytes(b"firmware")
    monkeypatch.setenv("CANIV_FIRMWARE_DIR", str(folder))
    return folder


@pytest.fixture
async def client(fake, firmware):
    async with Client(mcp) as c:
        yield c


def ran(fake, flag):
    return [c for c in fake.calls if flag in c]


async def test_the_server_offers_six_tools(client):
    assert sorted(t.name for t in await client.list_tools()) == ["caniv_blink", "caniv_configure", "caniv_firmware", "caniv_flash", "caniv_info", "caniv_list"]


async def test_the_core_toolset_hides_the_two_tools_that_change_a_device(client, monkeypatch):
    monkeypatch.setenv("CANIV_TOOLSET", "core")
    try:
        apply_toolset()
        async with Client(mcp) as core:
            assert sorted(t.name for t in await core.list_tools()) == ["caniv_blink", "caniv_firmware", "caniv_info", "caniv_list"]
    finally:
        apply_toolset("full")
    monkeypatch.setenv("CANIV_TOOLSET", "tiny")
    with pytest.raises(CtreError, match="CANIV_TOOLSET must be 'full' or 'core'"):
        apply_toolset()


# -- reading ---------------------------------------------------------------------------------------------------------


async def test_list_reads_the_rows_by_the_columns_of_the_header(client):
    out = await call(client, "caniv_list")
    assert out["total"] == 2 and out["devices"][0] == {"serial": "0123ABCD", "name": "robot-bus", "status": "Application", "app": "26.1.0.0"}
    assert out["devices"][1]["name"] == "spare" and "hint" not in out


async def test_list_with_no_device_gives_the_header_only_result_seen_on_a_real_caniv_and_a_hint(client, fake):
    fake.devices = ()
    out = await call(client, "caniv_list")
    assert out["total"] == 0 and out["devices"] == [] and "Connect one by USB" in out["hint"] and "raw" not in out


async def test_list_shows_caniv_lines_it_could_not_parse_instead_of_hiding_them(client, fake):
    fake.list_text = "No header here\nABC123  something"
    out = await call(client, "caniv_list")
    assert out["total"] == 0 and out["raw"] == ["No header here", "ABC123 something"]  # nothing is dropped: only caniv's own header row is left out


async def test_the_header_row_alone_is_not_shown_as_unparsed_output(client, fake):
    fake.list_text = HEADER + "\n"
    assert "raw" not in await call(client, "caniv_list")


async def test_info_runs_info_and_license_for_the_named_device_and_returns_caniv_lines(client, fake):
    out = await call(client, "caniv_info", {"device": "0123ABCD"})
    assert out["device"] == "0123ABCD" and out["firmware"] == "26.70.0.0"  # the Application Firmware, which .crf files carry
    assert out["info"]["USB Firmware Vers"] == "26.6.0" and out["info"]["CAN Bus Termination"] == "true"
    assert not {"USB Serial Number", "USB Name", "USB Discovery Status", "Reset Flags", "V+ Supply (V)", "Manufacture Date"} & set(out["info"])  # left out unless full
    assert out["license"] == ["Number of Licenses: 1", "License 0: LIC-00-00000000-B-FRC"]
    assert sorted(c[:3] for c in fake.calls[1:]) == [["-d", "0123ABCD", "--info"], ["-d", "0123ABCD", "--license"]]  # after the list that resolved the device


async def test_info_without_a_device_uses_any(client, fake):
    assert (await call(client, "caniv_info"))["device"] == "any" and fake.calls[0][:2] == ["--any", "--info"]


async def test_caniv_errors_become_plain_messages_with_the_code_and_a_hint(client, fake):
    fake.devices = ()
    with pytest.raises(ToolError, match=r"caniv info failed: Could not open device \(error -102\)\. No CANivore could be opened: is one connected by USB"):
        await call(client, "caniv_info")


async def test_an_unknown_error_text_is_still_reported_with_the_exit_code():
    assert "(exit 3): odd" in str(caniv.failure(RunResult(3, "odd", False), "list"))
    assert caniv.failure(RunResult(0, "fine", False), "list") is None


# -- device names ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("device", ["*", " * ", "has space", "a;b", "x" * 33, "", "../x"])
async def test_a_device_name_that_could_mean_everything_or_inject_something_is_refused(client, fake, device):
    with pytest.raises(ToolError, match="CANivore"):
        await call(client, "caniv_blink", {"device": device})
    assert fake.calls == []


async def test_a_change_aimed_at_a_device_caniv_does_not_list_is_refused_naming_the_ones_it_does(client, fake):
    with pytest.raises(ToolError, match=r"does not list a CANivore 'typo'\. It lists: 0123ABCD \(robot-bus\), 0456EF \(spare\)"):
        await call(client, "caniv_blink", {"device": "typo"})
    assert ran(fake, "--blink") == []


async def test_a_device_may_be_named_by_serial_or_by_name_in_any_case_and_caniv_gets_the_serial_it_printed(client, fake):
    assert (await call(client, "caniv_blink", {"device": "ROBOT-BUS"}))["device"] == "0123ABCD"
    await call(client, "caniv_blink", {"device": "0456ef"})
    assert [c[:3] for c in ran(fake, "--blink")] == [["-d", "0123ABCD", "--blink"], ["-d", "0456EF", "--blink"]]


async def test_when_the_list_layout_is_unknown_a_device_is_accepted_only_as_a_whole_word_of_the_raw_text(client, fake):
    fake.list_text = "0123ABCD robot-bus Application 26.1.0.0"
    assert (await call(client, "caniv_blink", {"device": "0123ABCD"}))["blinked"] is True
    with pytest.raises(ToolError, match="does not list"):
        await call(client, "caniv_blink", {"device": "0123"})  # part of a word is not the device


# -- changing a device -----------------------------------------------------------------------------------------------


async def test_rename_and_reset_are_refused_until_the_owner_allows_them_and_nothing_runs(client, fake):
    with pytest.raises(ToolError, match="CANIV_ALLOW_CONFIG=1"):
        await call(client, "caniv_configure", {"device": "0123ABCD", "reset": True})
    assert fake.calls == []


async def test_rename_runs_caniv_name_and_says_a_reset_is_needed(client, fake, monkeypatch):
    monkeypatch.setenv("CANIV_ALLOW_CONFIG", "1")
    out = await call(client, "caniv_configure", {"device": "0123ABCD", "name": "drive-bus"})
    assert out == {"device": "0123ABCD", "renamed": "drive-bus", "note": "The new name takes effect after a reset."}
    assert ran(fake, "--name")[0][:4] == ["-d", "0123ABCD", "--name", "drive-bus"] and ran(fake, "--reset") == []


async def test_rename_then_reset_runs_them_in_that_order(client, fake, monkeypatch):
    monkeypatch.setenv("CANIV_ALLOW_CONFIG", "1")
    out = await call(client, "caniv_configure", {"device": "0123ABCD", "name": "drive-bus", "reset": True})
    assert out == {"device": "0123ABCD", "renamed": "drive-bus", "reset": True}
    assert [c[2] for c in fake.calls if c[0] == "-d"] == ["--name", "--reset"]


async def test_configure_needs_something_to_do_a_sane_name_and_a_listed_device(client, fake, monkeypatch):
    monkeypatch.setenv("CANIV_ALLOW_CONFIG", "1")
    with pytest.raises(ToolError, match="Give a new `name`"):
        await call(client, "caniv_configure", {"device": "0123ABCD"})
    with pytest.raises(ToolError, match="not allowed as a CANivore name"):
        await call(client, "caniv_configure", {"device": "0123ABCD", "name": "bad name!"})
    with pytest.raises(ToolError, match="does not list"):
        await call(client, "caniv_configure", {"device": "nope", "reset": True})
    assert ran(fake, "--name") == [] and ran(fake, "--reset") == []


async def test_a_caniv_failure_during_a_change_is_reported(client, fake, monkeypatch):
    monkeypatch.setenv("CANIV_ALLOW_CONFIG", "1")

    async def fails_on_reset(exe, args, timeout_s):
        if "--reset" in args:
            return RunResult(127, NO_DEVICE, False)
        return await fake(exe, args, timeout_s)

    monkeypatch.setattr(caniv, "run_tool", fails_on_reset)
    with pytest.raises(ToolError, match="caniv reset failed: Could not open device"):
        await call(client, "caniv_configure", {"device": "0123ABCD", "reset": True})


# -- firmware --------------------------------------------------------------------------------------------------------


async def test_the_firmware_list_is_just_canivore_file_names_highest_version_first(client):
    out = await call(client, "caniv_firmware")
    assert out == {"total": 3, "files": ["CANivore-26.1.0.0-Season2026.crf", "CANivore-25.4.0.0-Season2025.crf", "CANivore-Application-23.2.4.0-Season2023.crf"]}
    assert "hint" not in out and "latest" not in out  # the folder was set, and there is no Tuner X firmware index here
    assert (await call(client, "caniv_firmware", {"limit": 1})) == {"total": 3, "files": ["CANivore-26.1.0.0-Season2026.crf"], "omitted": 2}


async def test_the_firmware_list_adds_ctres_latest_per_season_from_tuner_xs_saved_index(client, tmp_path, monkeypatch):
    cache = tmp_path / "Packages" / "CTRElectronics.PhoenixTunerXPreview_x" / "LocalCache"
    cache.mkdir(parents=True)
    (cache / "firmware-index.json").write_text(json.dumps({"Releases": {"v6": {"2026": [{"Device": "CANivore", "LatestVersion": "26.1.0.0", "LatestRelease": "CANivore-26.1.0.0-Season2026.crf"}]}}}), encoding="utf-8")
    monkeypatch.setenv("CTRE_TUNERX_DIR", str(tmp_path / "Packages"))
    assert (await call(client, "caniv_firmware"))["latest"] == {"2026": {"latest": "26.1.0.0", "file": "CANivore-26.1.0.0-Season2026.crf"}}


async def test_flash_is_refused_until_the_owner_allows_it_and_nothing_runs(client, fake):
    with pytest.raises(ToolError, match="CANIV_ALLOW_FLASH=1"):
        await call(client, "caniv_flash", {"device": "0123ABCD", "file": "CANivore-26.1.0.0-Season2026.crf"})
    assert fake.calls == []


async def test_flash_runs_caniv_with_the_resolved_file_and_the_long_timeout(client, fake, firmware, monkeypatch):
    monkeypatch.setenv("CANIV_ALLOW_FLASH", "1")
    out = await call(client, "caniv_flash", {"device": "0123ABCD", "file": "CANivore-26.1.0.0-Season2026.crf"})
    assert out["device"] == "0123ABCD" and out["file"] == "CANivore-26.1.0.0-Season2026.crf" and "caniv_info" in out["next"]
    assert ran(fake, "--flash")[0] == ["-d", "0123ABCD", "--flash", str(firmware / "CANivore-26.1.0.0-Season2026.crf"), "timeout=300"]


async def test_flash_takes_only_canivore_files_from_the_allowed_folder(client, fake, tmp_path, monkeypatch):
    monkeypatch.setenv("CANIV_ALLOW_FLASH", "1")
    with pytest.raises(ToolError, match="is not a CANivore firmware file"):
        await call(client, "caniv_flash", {"device": "0123ABCD", "file": "TalonFXS-26.3.0.1-Phoenix6.crf"})  # another device's firmware
    outside = tmp_path / "CANivore-9.9.9.9-x.crf"
    outside.write_bytes(b"x")
    with pytest.raises(ToolError, match="outside the folders this server may read"):
        await call(client, "caniv_flash", {"device": "0123ABCD", "file": str(outside)})
    with pytest.raises(ToolError, match=r"Only \.crf files"):
        await call(client, "caniv_flash", {"device": "0123ABCD", "file": "notes.txt"})
    with pytest.raises(ToolError, match="CANivore"):
        await call(client, "caniv_flash", {"device": "*", "file": "CANivore-26.1.0.0-Season2026.crf"})
    assert ran(fake, "--flash") == []


async def test_flash_to_a_device_that_is_not_listed_never_starts(client, fake, monkeypatch):
    monkeypatch.setenv("CANIV_ALLOW_FLASH", "1")
    with pytest.raises(ToolError, match="does not list a CANivore 'nope'"):
        await call(client, "caniv_flash", {"device": "nope", "file": "CANivore-26.1.0.0-Season2026.crf"})
    assert ran(fake, "--flash") == []


async def test_a_firmware_file_caniv_cannot_read_is_reported_with_its_code(client, firmware, monkeypatch):
    monkeypatch.setenv("CANIV_ALLOW_FLASH", "1")
    (firmware / "CANivore-1.0.0.0-Bad.crf").write_bytes(b"")
    with pytest.raises(ToolError, match=r"caniv flash failed: CRF could not be read \(error -206\)"):
        await call(client, "caniv_flash", {"device": "0123ABCD", "file": "CANivore-1.0.0.0-Bad.crf"})


# -- no caniv --------------------------------------------------------------------------------------------------------


async def test_without_caniv_installed_the_error_says_how_to_get_it(client, tools_dir):
    for path in tools_dir.rglob("caniv-*"):
        path.unlink()
    with pytest.raises(ToolError, match="No caniv is installed.*ctre_install tool=caniv"):
        await call(client, "caniv_list")


async def test_on_macos_every_tool_says_caniv_is_not_available_there(client, tools_dir, monkeypatch):
    for path in tools_dir.rglob("caniv-*"):
        path.unlink()
    monkeypatch.setenv("CTRE_PLATFORM", "macosuniversal")
    for tool, args in (("caniv_list", {}), ("caniv_info", {}), ("caniv_blink", {"device": "0123ABCD"})):
        with pytest.raises(ToolError, match="not available on macOS"):
            await call(client, tool, args)


async def test_a_firmware_file_saved_by_two_tuner_x_apps_is_listed_once(client, tmp_path, monkeypatch):
    packages = tmp_path / "Packages"
    for app in ("CTRElectronics.209251697EEC9_x", "CTRElectronics.PhoenixTunerXPreview_x"):
        cache = packages / app / "LocalCache"
        cache.mkdir(parents=True)
        (cache / "CANivore-26.1.0.0-Season2026.crf").write_bytes(b"firmware")
    monkeypatch.delenv("CANIV_FIRMWARE_DIR")
    monkeypatch.setenv("CTRE_TUNERX_DIR", str(packages))
    out = await call(client, "caniv_firmware")
    assert out == {"total": 1, "files": ["CANivore-26.1.0.0-Season2026.crf"]}  # a name once; no hint while there is something to show
    for path in packages.rglob("*.crf"):
        path.unlink()
    assert (await call(client, "caniv_firmware"))["hint"] == "CANIV_FIRMWARE_DIR is not set, so firmware files are read from CTRE Tuner X's saved files."


def test_the_real_list_layout_with_a_wide_serial_column_is_read_by_the_headers_positions():
    rows = caniv.parse_list(REAL_LIST)
    assert rows == [{"serial": "00000000000000000000000000000001", "name": "canfd", "status": "Application", "app": "26.6.0"}]
    assert caniv.listed(REAL_LIST, rows, "CANFD") == "00000000000000000000000000000001"  # by name, any case: caniv gets the serial it printed
    assert caniv.parse_list(f"{'Serial Num':<34}{'Name':<20}{'Status':<20}{'Application Vers':<20}\r\n") == []  # header only: no device (seen)
    assert caniv.parse_list("") == [] and caniv.parse_list("Name Serial Num") == []


def test_caniv_lines_lose_their_tabs_and_padding_and_fields_keep_caniv_own_keys():
    assert caniv.lines(REAL_LICENSE) == ["Number of Licenses: 1", "License 0: LIC-00-00000000-B-FRC"]
    pairs = caniv.fields(REAL_INFO)
    assert pairs["Application Firmware"] == "26.70.0.0" and pairs["V+ Supply (V)"] == "0.0867577" and "" not in pairs
    assert len(caniv.lines("x\n" * 100)) == caniv.MAX_LINES


async def test_info_without_an_application_firmware_line_still_returns_what_caniv_said(client, fake, monkeypatch):
    async def odd(exe, args, timeout_s):
        return RunResult(0, "\tSomething new:   1\n", False)

    monkeypatch.setattr(caniv, "run_tool", odd)
    out = await call(client, "caniv_info")
    assert "firmware" not in out and out["info"] == {"Something new": "1"}


async def test_info_for_a_name_in_another_letter_case_reaches_caniv_as_the_serial_it_printed(client, fake):
    out = await call(client, "caniv_info", {"device": "ROBOT-BUS"})
    assert out["firmware"] == "26.70.0.0" and ["-d", "0123ABCD", "--info"] in [c[:3] for c in fake.calls] and not [c for c in ran(fake, "ROBOT-BUS") if c[0] == "-d"]


async def test_info_for_a_device_that_is_not_listed_is_refused_before_asking_it(client, fake):
    with pytest.raises(ToolError, match="does not list a CANivore 'typo'"):
        await call(client, "caniv_info", {"device": "typo"})
    assert ran(fake, "--info") == []


@pytest.mark.parametrize(("tool", "args", "env"), [("caniv_blink", {"device": "robot-bus"}, {}),
                                                  ("caniv_info", {"device": "robot-bus"}, {}),
                                                  ("caniv_configure", {"device": "robot-bus", "name": "x", "reset": True}, {"CANIV_ALLOW_CONFIG": "1"}),
                                                  ("caniv_flash", {"device": "robot-bus", "file": "CANivore-26.1.0.0-Season2026.crf"}, {"CANIV_ALLOW_FLASH": "1"})])
async def test_the_installed_caniv_is_resolved_once_per_tool_call(client, monkeypatch, tool, args, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    resolved = []
    original = caniv.newest_installed_async

    async def counting(name):
        resolved.append(name)
        return await original(name)

    monkeypatch.setattr(caniv, "newest_installed_async", counting)
    await call(client, tool, args)
    assert resolved == ["caniv"]  # not once per caniv process (list, then the action, then the license...)


async def test_caniv_runs_never_overlap_even_when_the_assistant_calls_several_tools_together(client, fake, monkeypatch):
    import asyncio

    running = []
    peak = []

    async def slow(exe, args, timeout_s):
        running.append(1)
        peak.append(len(running))
        await asyncio.sleep(0.02)
        running.pop()
        return await fake(exe, args, timeout_s)

    monkeypatch.setattr(caniv, "run_tool", slow)
    await asyncio.gather(call(client, "caniv_info", {"device": "robot-bus"}), call(client, "caniv_list"), call(client, "caniv_blink", {"device": "spare"}))
    assert peak and max(peak) == 1  # a real CANivore answers the second of two overlapping runs with -121


async def test_the_usb_busy_error_gets_its_own_hint(client, fake, monkeypatch):
    async def busy(exe, args, timeout_s):
        return RunResult(4294967175, 'Error value:-121\nError Message:"Could not open USB connection"', False)  # exit code and text as seen

    monkeypatch.setattr(caniv, "run_tool", busy)
    with pytest.raises(ToolError, match=r"Could not open USB connection \(error -121\)\. The USB connection could not be opened: another program may be using"):
        await call(client, "caniv_list")


async def test_info_full_adds_the_keys_left_out_by_default(client):
    out = await call(client, "caniv_info", {"device": "0123ABCD", "full": True})
    assert {"USB Serial Number", "USB Name", "USB Discovery Status", "V+ Supply (V)"} <= set(out["info"])
    compact = await call(client, "caniv_info", {"device": "0123ABCD"})
    assert len(str(compact)) < len(str(out)) and compact["firmware"] == out["firmware"]


async def test_a_key_caniv_adds_later_is_kept_in_the_compact_info(client, fake, monkeypatch):
    async def newer(exe, args, timeout_s):
        return RunResult(0, "\tApplication Firmware:  26.70.0.0\n\tSome New Key:  yes\n\tReset Flags:  0x1\n", False)

    monkeypatch.setattr(caniv, "run_tool", newer)
    assert (await call(client, "caniv_info"))["info"] == {"Application Firmware": "26.70.0.0", "Some New Key": "yes"}


async def test_blink_says_nothing_extra_when_caniv_prints_nothing(client):
    assert await call(client, "caniv_blink", {"device": "robot-bus"}) == {"device": "0123ABCD", "blinked": True}


@pytest.mark.parametrize("name", ["-x", "--help", "-", "*", "", " ", "a b", "x" * 33, "a\n", "_x"])
def test_a_device_name_that_could_look_like_an_option_or_is_malformed_is_refused(name):
    with pytest.raises(CtreError):
        caniv.check_device(name)
    with pytest.raises(CtreError):
        caniv.check_new_name(name)


@pytest.mark.parametrize("name", ["canfd", "0123456789ABCDEF0123456789ABCDEF", "x" * 32, "a", "Bus_1", "can.fd-2", "9lives"])
def test_a_serial_number_or_plain_name_is_accepted(name):
    assert caniv.check_device(name) == name and caniv.check_new_name(name) == name


def test_an_unparsed_list_only_matches_a_device_that_starts_a_line_and_not_an_error_word():
    raw = 'Error value:-102\nError Message:"Could not open device"\nUnknown option device\n'
    for word in ("device", "Could", "open", "Error", "Message"):
        assert caniv.listed(raw, [], word) is None
    assert caniv.listed("0123ABCD something odd\n", [], "0123abcd") == "0123ABCD" or caniv.listed("0123ABCD something odd\n", [], "0123abcd") == "0123abcd"

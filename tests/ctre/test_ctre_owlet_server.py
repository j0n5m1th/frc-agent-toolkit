"""The Owlet server through the MCP protocol, with a stand-in for Owlet that behaves the way the real one was seen to (see frc_mcp_ctre_owlet/owlet.py)."""

import hashlib

import pytest
from ctre_helpers import call, fake_install
from fastmcp import Client
from fastmcp.exceptions import ToolError

from frc_mcp_ctre.runner import RunResult
from frc_mcp_ctre.store import write_sidecar
from frc_mcp_ctre_owlet import owlet
from frc_mcp_ctre_owlet.spec import SPEC

mcp, _ = SPEC.load()

SIGNALS = (("RobotEnable", "ff0001"), ("DriveState/Pose", "ff0005"), ("Phoenix6/drive/TalonFX-0/Velocity", "10006fd"),
           ("Phoenix6/drive/TalonFX-0/Position", "10006fe"), ("Phoenix6/turret/TalonFX-5/Velocity", "10506fd"))
SCAN = "".join(f"{name + ':':<74}{ident}\n" for name, ident in SIGNALS)  # Owlet pads names so ids start at column 74


class FakeOwlet:
    """Answers --compliancy, --check-pro and --scan from fixed text; converts by writing a small file. Records every call."""

    def __init__(self, fail_convert=False):
        self.fail_convert = fail_convert
        self.calls: list[list[str]] = []

    async def __call__(self, exe, args, timeout_s):
        self.calls.append([exe.name, *args])
        if "--compliancy" in args:  # only asked of an Owlet whose level is in neither the index nor its file name
            return RunResult(0, "owlet: 19\nhoot log: 19", False)
        if "--check-pro" in args:
            return RunResult(0, "hoot file is Pro-licensed", False)
        if "--scan" in args:
            return RunResult(0, SCAN, False)
        if self.fail_convert:
            return RunResult(0, "DataLog: Could not open log file, no log being saved\nOutput has been written to x", False)
        target = args[1]
        open(target, "wb").write(b"x" * 2048)  # noqa: SIM115
        if "--split" in args:
            open(target.replace(".wpilog", ".2.wpilog"), "wb").write(b"y")  # noqa: SIM115
        return RunResult(0, "Extracting...\nOutput has been written to " + target, False)


def write_hoot(path, level):
    """A stand-in hoot file: the compliancy is the byte at offset 70, as in a real one."""
    path.write_bytes(bytes(70) + bytes([level]) + b"rest of the file")


@pytest.fixture
def hoots(tmp_path, monkeypatch):
    folder = tmp_path / "hoots"
    folder.mkdir()
    write_hoot(folder / "match1.hoot", 19)
    monkeypatch.setenv("OWLET_DIR", str(folder))
    return folder


@pytest.fixture
def fake(monkeypatch, tools_dir, index_file):
    fake_install(tools_dir, "owlet", "owlet", "26.3.0")
    stand_in = FakeOwlet()
    monkeypatch.setattr(owlet, "run_tool", stand_in)
    return stand_in


@pytest.fixture
async def client(hoots, fake):
    async with Client(mcp) as c:
        yield c


async def test_the_server_offers_four_tools(client):
    assert sorted(t.name for t in await client.list_tools()) == ["owlet_convert", "owlet_info", "owlet_list", "owlet_signals"]


async def test_list_shows_hoot_files_only(client, hoots):
    (hoots / "notes.txt").write_text("x", encoding="utf-8")
    out = await call(client, "owlet_list")
    assert out["total"] == 1 and out["cols"] == ["file", "mb", "modified", "c"] and out["rows"][0][0] == "match1.hoot" and out["rows"][0][3] == 19


async def test_info_says_which_installed_owlet_reads_the_log(client):
    out = await call(client, "owlet_info", {"file": "match1.hoot"})
    assert out["compliancy"] == 19 and out["owlet"] == "26.3.0 (ctre)" and out["pro"] is True


async def test_pro_is_false_when_owlet_says_not_pro_licensed(client, fake, monkeypatch):
    async def not_pro(exe, args, timeout_s):
        if "--check-pro" in args:
            return RunResult(0, "hoot file is NOT Pro-licensed", False)
        return await fake(exe, args, timeout_s)

    monkeypatch.setattr(owlet, "run_tool", not_pro)
    assert (await call(client, "owlet_info", {"file": "match1.hoot"}))["pro"] is False


async def test_the_owlet_of_exactly_the_logs_level_is_used_never_a_neighbouring_one(client, tools_dir, hoots, fake):
    fake_install(tools_dir, "owlet", "owlet", "26.70.0")  # level 21
    fake_install(tools_dir, "owlet", "owlet", "26.0.0")  # level 18
    assert (await call(client, "owlet_info", {"file": "match1.hoot"}))["owlet"].startswith("26.3.0")
    write_hoot(hoots / "match1.hoot", 21)
    assert (await call(client, "owlet_info", {"file": "match1.hoot"}))["owlet"].startswith("26.70.0")
    assert not any("--compliancy" in c for c in fake.calls)  # the level comes from the file header and the index: no extra process


async def test_a_log_whose_level_is_not_installed_names_the_exact_install_command(client, hoots):
    write_hoot(hoots / "match1.hoot", 21)
    with pytest.raises(ToolError, match=r"compliancy 21 log.*exactly that level.*Installed Owlets are for compliancy 19.*ctre_install tool=owlet compliancy=21.*Owlet 26\.70\.0"):
        await call(client, "owlet_info", {"file": "match1.hoot"})


async def test_a_level_ctre_has_no_owlet_for_says_so(client, hoots):
    write_hoot(hoots / "match1.hoot", 20)
    with pytest.raises(ToolError, match="no Owlet for that level"):
        await call(client, "owlet_info", {"file": "match1.hoot"})


async def test_a_file_that_is_not_a_hoot_is_refused_by_its_header(client, hoots):
    (hoots / "short.hoot").write_bytes(b"tiny")
    write_hoot(hoots / "old.hoot", 3)
    with pytest.raises(ToolError, match="too short"):
        await call(client, "owlet_info", {"file": "short.hoot"})
    with pytest.raises(ToolError, match="older than Owlet can read"):
        await call(client, "owlet_info", {"file": "old.hoot"})


async def test_an_owlet_that_is_in_neither_the_index_nor_a_file_name_is_asked_for_its_level(client, tools_dir, fake):
    path = tools_dir / "owlet" / "99.0.0" / "owlet-99.0.0-windowsx86-64"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    write_sidecar(path, hashlib.sha256(b"").hexdigest())  # as `ctre_install` leaves it: the index does not list this version, so the install record is what vouches for it
    out = await call(client, "owlet_info", {"file": "match1.hoot"})
    assert out["owlet"].startswith("99.0.0") and any("--compliancy" in c for c in fake.calls)


async def test_without_any_owlet_the_error_says_how_to_get_one(client, tools_dir):
    for path in tools_dir.rglob("owlet-*"):
        path.unlink()
    with pytest.raises(ToolError, match="No Owlet is installed.*ctre_install tool=owlet compliancy=19"):
        await call(client, "owlet_info", {"file": "match1.hoot"})


async def test_signals_are_name_id_pairs_with_the_biggest_device_groups(client):
    out = await call(client, "owlet_signals", {"file": "match1.hoot"})
    assert out["total"] == 5 and out["signals"][1] == ["DriveState/Pose", "ff0005"]
    assert out["groups"] == {"Phoenix6/drive/TalonFX-0": 2, "Phoenix6/turret/TalonFX-5": 1, "DriveState": 1, "RobotEnable": 1}


async def test_signals_can_be_filtered_and_paged(client):
    out = await call(client, "owlet_signals", {"file": "match1.hoot", "name_contains": "velocity", "limit": 1})
    assert out["matches"] == 2 and out["signals"] == [["Phoenix6/drive/TalonFX-0/Velocity", "10006fd"]] and out["next_offset"] == 1 and "groups" not in out
    page = await call(client, "owlet_signals", {"file": "match1.hoot", "name_contains": "velocity", "limit": 1, "offset": 1})
    assert page["signals"] == [["Phoenix6/turret/TalonFX-5/Velocity", "10506fd"]] and "next_offset" not in page


async def test_convert_exports_only_the_chosen_signals_beside_the_hoot_file(client, hoots, fake):
    out = await call(client, "owlet_convert", {"file": "match1.hoot", "signals": ["ff0005", "Phoenix6/drive/TalonFX-0/Velocity"]})
    assert out["file"] == "match1.wpilog" and out["signals"] == 2 and (hoots / "match1.wpilog").is_file() and out["mb"] >= 0 and "folder" not in out
    run = fake.calls[-1]
    assert run[1:3] == [str(hoots / "match1.hoot"), str(hoots / "match1.wpilog")] and run[run.index("-s") + 1] == "ff0005,10006fd" and run[run.index("-f") + 1] == "wpilog"


async def test_convert_by_name_part_and_a_chosen_output_name(client, hoots, fake):
    out = await call(client, "owlet_convert", {"file": "match1.hoot", "name_contains": "TalonFX-0", "out": "drive"})
    assert out["file"] == "drive.wpilog" and out["signals"] == 2 and (hoots / "drive.wpilog").is_file()


async def test_convert_will_not_start_without_a_choice_of_signals(client, fake):
    with pytest.raises(ToolError, match="Say which signals"):
        await call(client, "owlet_convert", {"file": "match1.hoot"})
    assert not any("-f" in c for c in fake.calls)


async def test_an_unknown_signal_stops_the_run_because_owlet_would_export_everything(client, hoots, fake):
    with pytest.raises(ToolError, match="Not signals of this file: 'zzz'"):
        await call(client, "owlet_convert", {"file": "match1.hoot", "signals": ["zzz", "ff0005"]})
    assert not any("-f" in c for c in fake.calls) and not (hoots / "match1.wpilog").exists()


async def test_all_signals_must_be_asked_for_and_not_mixed_with_a_choice(client, fake):
    with pytest.raises(ToolError, match="alone"):
        await call(client, "owlet_convert", {"file": "match1.hoot", "all_signals": True, "signals": ["ff0005"]})
    out = await call(client, "owlet_convert", {"file": "match1.hoot", "all_signals": True})
    assert out["signals"] == "all" and "-s" not in fake.calls[-1]


async def test_an_existing_output_is_never_overwritten(client, hoots):
    (hoots / "match1.wpilog").write_bytes(b"precious")
    with pytest.raises(ToolError, match="never overwritten"):
        await call(client, "owlet_convert", {"file": "match1.hoot", "signals": ["ff0005"]})
    assert (hoots / "match1.wpilog").read_bytes() == b"precious"


@pytest.mark.parametrize("name", ["../escape", "sub/dir", ".."])
async def test_the_output_name_cannot_leave_the_hoot_folder(client, name):
    with pytest.raises(ToolError, match="plain file name"):
        await call(client, "owlet_convert", {"file": "match1.hoot", "signals": ["ff0005"], "out": name})


async def test_a_conversion_whose_output_never_appeared_is_an_error_not_a_success(client, fake):
    fake.fail_convert = True
    with pytest.raises(ToolError, match="Owlet failed.*Could not open log file"):
        await call(client, "owlet_convert", {"file": "match1.hoot", "signals": ["ff0005"]})


async def test_files_owlet_split_off_are_reported(client, fake, monkeypatch):
    original = fake.__call__

    async def splitting(exe, args, timeout_s):
        return await original(exe, [*args, "--split"] if "-f" in args else args, timeout_s)

    monkeypatch.setattr(owlet, "run_tool", splitting)
    out = await call(client, "owlet_convert", {"file": "match1.hoot", "signals": ["ff0005"]})
    assert out["also"] == ["match1.2.wpilog"]


async def test_only_hoot_files_inside_the_allowed_folder_can_be_read(client, tmp_path):
    outside = tmp_path / "other.hoot"
    outside.write_bytes(b"x")
    with pytest.raises(ToolError, match="outside the folders this server may read"):
        await call(client, "owlet_info", {"file": str(outside)})
    with pytest.raises(ToolError, match="Only .hoot files"):
        await call(client, "owlet_info", {"file": "match1.wpilog"})
    with pytest.raises(ToolError, match="No hoot file named 'nope.hoot'"):
        await call(client, "owlet_info", {"file": "nope.hoot"})


async def test_a_scan_owlet_refuses_is_reported_with_its_own_words(client, fake, monkeypatch):
    async def refuses(exe, args, timeout_s):
        if "--scan" in args:
            return RunResult(1, "Input file is an unsupported version: 1", False)  # seen, exit code 1
        return await fake(exe, args, timeout_s)

    monkeypatch.setattr(owlet, "run_tool", refuses)
    with pytest.raises(ToolError, match=r"could not scan match1.hoot \(exit 1\): Input file is an unsupported version"):
        await call(client, "owlet_signals", {"file": "match1.hoot"})


def test_the_scan_parser_follows_the_files_own_padding_and_drops_message_lines():
    narrow = "".join(f"{name + ':':<58}{ident}\n" for name, ident in (("RobotMode", "1ff00"), ("DS:IsDSAttached", "3ff00"), ("Phoenix6/can_s2/CANBusIsUp", "ff0002")))
    assert [(s.name, s.id) for s in owlet.parse_scan(narrow)] == [("RobotMode", "1ff00"), ("DS:IsDSAttached", "3ff00"), ("Phoenix6/can_s2/CANBusIsUp", "ff0002")]
    assert [s.name for s in owlet.parse_scan("Extracting a.hoot:   12\n" + narrow)] == ["RobotMode", "DS:IsDSAttached", "Phoenix6/can_s2/CANBusIsUp"]
    assert owlet.parse_scan("Input file is an unsupported version: 2") == [] and owlet.parse_scan("") == []
    assert [s.name for s in owlet.parse_scan("OnlySignal:                  ff0001\n")] == ["OnlySignal"]


async def test_the_listing_shows_each_files_compliancy_and_survives_a_bad_file(client, hoots):
    (hoots / "broken.hoot").write_bytes(b"tiny")
    write_hoot(hoots / "newer.hoot", 21)
    rows = {r[0]: r[3] for r in (await call(client, "owlet_list"))["rows"]}
    assert rows == {"match1.hoot": 19, "newer.hoot": 21, "broken.hoot": None}


async def test_a_conversion_that_times_out_removes_what_it_half_wrote(client, hoots, fake, monkeypatch):
    from frc_mcp_ctre.errors import CtreError

    async def times_out(exe, args, timeout_s):
        if "-f" in args:
            open(args[1], "wb").write(b"half")  # noqa: SIM115
            open(args[1].replace(".wpilog", ".2.wpilog"), "wb").write(b"half")  # noqa: SIM115
            raise CtreError("owlet did not finish within 1 s and was stopped.")
        return await fake(exe, args, timeout_s)

    monkeypatch.setattr(owlet, "run_tool", times_out)
    with pytest.raises(ToolError, match="did not finish"):
        await call(client, "owlet_convert", {"file": "match1.hoot", "signals": ["ff0005"]})
    assert sorted(p.name for p in hoots.iterdir()) == ["match1.hoot"]  # the user's own file is untouched, nothing else remains


async def test_a_failed_conversion_removes_the_split_files_too(client, hoots, fake, monkeypatch):
    async def fails(exe, args, timeout_s):
        if "-f" in args:
            open(args[1], "wb").write(b"x")  # noqa: SIM115
            open(args[1].replace(".wpilog", ".2.wpilog"), "wb").write(b"x")  # noqa: SIM115
            return RunResult(1, "disk full", False)
        return await fake(exe, args, timeout_s)

    monkeypatch.setattr(owlet, "run_tool", fails)
    with pytest.raises(ToolError, match="Owlet failed .exit 1.: disk full"):
        await call(client, "owlet_convert", {"file": "match1.hoot", "signals": ["ff0005"]})
    assert sorted(p.name for p in hoots.iterdir()) == ["match1.hoot"]


async def test_finding_a_file_by_bare_name_does_not_block_the_event_loop(client, hoots, monkeypatch):
    import asyncio
    import time

    from frc_mcp_ctre import folders

    (hoots / "sub").mkdir()
    write_hoot(hoots / "sub" / "match2.hoot", 19)
    walk = folders.walk_folders

    def slow_walk(roots):
        time.sleep(0.5)  # a big tree
        return walk(roots)

    monkeypatch.setattr(folders, "walk_folders", slow_walk)
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        out = await call(client, "owlet_info", {"file": "match2.hoot"})
    finally:
        beat.cancel()
    assert out["file"] == "match2.hoot" and ticks >= 10  # the loop kept running while the walk slept

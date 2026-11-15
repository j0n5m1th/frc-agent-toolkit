"""The Passerine server through the MCP protocol, with a stand-in for passerine that behaves the way the real one was seen to (see frc_mcp_ctre_passerine/passerine.py)."""


import pytest
from ctre_helpers import call, fake_install
from fastmcp import Client
from fastmcp.exceptions import ToolError

from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.runner import RunResult
from frc_mcp_ctre_passerine import passerine
from frc_mcp_ctre_passerine.spec import SPEC

mcp, _ = SPEC.load()

MIDI = b"MThd" + bytes(10)


class FakePasserine:
    """Writes a small file for a MIDI input and fails like the real one for anything else. Records every call; overwrites like the real one (the server must not let it)."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.mode = "ok"

    async def __call__(self, exe, args, timeout_s):
        self.calls.append([exe.name, *args, f"timeout={timeout_s:g}"])
        source, target = args[1], args[3]
        if self.mode == "timeout":
            open(target, "wb").write(b"half")  # noqa: SIM115
            raise CtreError("passerine did not finish within 1 s and was stopped.")
        if self.mode == "fail":
            return RunResult(1, "Error: The system cannot find the file specified. (os error 2)", False)
        if self.mode == "silent":
            return RunResult(0, "Output has been written to " + target, False)  # seen for the other CTRE tools: success reported, no file
        if self.mode == "empty":
            open(target, "wb").write(b"")  # noqa: SIM115
            return RunResult(0, "Output has been written to " + target, False)
        if open(source, "rb").read(4) != b"MThd":  # noqa: SIM115
            return RunResult(1, "Error: invalid midi: not a midi file", False)
        open(target, "wb").write(b"\x00\x00\x00\x01" * 15)  # noqa: SIM115
        return RunResult(0, "Output has been written to " + target, False)


@pytest.fixture
def music(tmp_path, monkeypatch):
    folder = tmp_path / "music"
    folder.mkdir()
    (folder / "song.mid").write_bytes(MIDI)
    monkeypatch.setenv("PASSERINE_DIR", str(folder))
    return folder


@pytest.fixture
def fake(monkeypatch, tools_dir, index_file):
    fake_install(tools_dir, "passerine", "passerine", "1.0.0")
    stand_in = FakePasserine()
    monkeypatch.setattr(passerine, "run_tool", stand_in)
    return stand_in


@pytest.fixture
async def client(music, fake):
    async with Client(mcp) as c:
        yield c


async def test_the_server_offers_two_tools(client):
    assert sorted(t.name for t in await client.list_tools()) == ["passerine_convert", "passerine_list"]


async def test_list_shows_midi_files_or_chrp_files(client, music):
    (music / "other.MIDI").write_bytes(MIDI)
    (music / "done.chrp").write_bytes(b"x")
    (music / "notes.txt").write_text("x", encoding="utf-8")
    listed = await call(client, "passerine_list")
    assert listed["cols"] == ["file", "mb", "modified"] and sorted(row[0] for row in listed["rows"]) == ["other.MIDI", "song.mid"]
    chrp = await call(client, "passerine_list", {"kind": "chrp"})
    assert [row[0] for row in chrp["rows"]] == ["done.chrp"] and chrp["total"] == 1


async def test_convert_writes_a_new_chrp_beside_the_midi_file(client, music, fake):
    out = await call(client, "passerine_convert", {"file": "song.mid"})
    assert out == {"file": "song.chrp", "kb": 0.1, "passerine": "1.0.0"}
    assert (music / "song.chrp").is_file()
    exe, flag_in, source, flag_out, target, timeout = fake.calls[-1]
    assert (flag_in, flag_out, timeout) == ("--input-midi", "--output-chrp", "timeout=60")
    assert source == str(music / "song.mid") and target == str(music / "song.chrp")  # absolute paths, the long flags: nothing is left to guess


async def test_convert_with_a_chosen_name(client, music):
    assert (await call(client, "passerine_convert", {"file": "song.mid", "out": "intro"}))["file"] == "intro.chrp"
    assert (music / "intro.chrp").is_file()


async def test_an_existing_output_is_never_overwritten_even_though_passerine_would(client, music, fake):
    (music / "song.chrp").write_bytes(b"precious")
    with pytest.raises(ToolError, match="never overwritten"):
        await call(client, "passerine_convert", {"file": "song.mid"})
    assert (music / "song.chrp").read_bytes() == b"precious" and fake.calls == []


@pytest.mark.parametrize("name", ["../escape", "sub/dir", ".."])
async def test_the_output_name_cannot_leave_the_music_folder(client, name):
    with pytest.raises(ToolError, match="plain file name"):
        await call(client, "passerine_convert", {"file": "song.mid", "out": name})


async def test_a_file_that_is_not_midi_is_refused_before_passerine_runs(client, music, fake):
    (music / "fake.mid").write_bytes(b"RIFF....WAVE")
    with pytest.raises(ToolError, match="is not a MIDI file"):
        await call(client, "passerine_convert", {"file": "fake.mid"})
    assert fake.calls == [] and not (music / "fake.chrp").exists()


async def test_passerines_own_error_is_reported_and_leaves_no_file(client, music, fake):
    fake.mode = "fail"
    with pytest.raises(ToolError, match=r"passerine 1\.0\.0 failed \(exit 1\): The system cannot find the file specified"):
        await call(client, "passerine_convert", {"file": "song.mid"})
    assert not (music / "song.chrp").exists()


@pytest.mark.parametrize("mode", ["silent", "empty"])
async def test_success_without_a_real_output_file_is_an_error(client, music, fake, mode):
    fake.mode = mode
    with pytest.raises(ToolError, match="failed"):
        await call(client, "passerine_convert", {"file": "song.mid"})
    assert not (music / "song.chrp").exists()


async def test_a_run_that_times_out_removes_the_half_written_file(client, music, fake):
    fake.mode = "timeout"
    with pytest.raises(ToolError, match="did not finish"):
        await call(client, "passerine_convert", {"file": "song.mid"})
    assert sorted(p.name for p in music.iterdir()) == ["song.mid"]


async def test_only_midi_files_inside_the_music_folder_can_be_read(client, tmp_path):
    outside = tmp_path / "elsewhere.mid"
    outside.write_bytes(MIDI)
    with pytest.raises(ToolError, match="outside the folders this server may read"):
        await call(client, "passerine_convert", {"file": str(outside)})
    with pytest.raises(ToolError, match=r"Only \.mid/\.midi files"):
        await call(client, "passerine_convert", {"file": "song.chrp"})
    with pytest.raises(ToolError, match="No MIDI file named 'nope.mid'.*passerine_list"):
        await call(client, "passerine_convert", {"file": "nope.mid"})


async def test_without_passerine_installed_the_error_says_how_to_get_it(client, tools_dir):
    for path in tools_dir.rglob("passerine-*"):
        path.unlink()
    with pytest.raises(ToolError, match="No passerine is installed.*ctre_install tool=passerine"):
        await call(client, "passerine_convert", {"file": "song.mid"})


async def test_the_newest_installed_passerine_is_used(client, tools_dir, fake):
    fake_install(tools_dir, "passerine", "passerine", "1.2.0")
    assert (await call(client, "passerine_convert", {"file": "song.mid"}))["passerine"] == "1.2.0"
    assert fake.calls[-1][0].startswith("passerine-1.2.0")


async def test_the_size_limit_stops_a_huge_midi_file(client, music, monkeypatch):
    monkeypatch.setenv("PASSERINE_MAX_MB", "1")
    (music / "huge.mid").write_bytes(MIDI + bytes(2 * 2**20))
    with pytest.raises(ToolError, match="over the PASSERINE_MAX_MB limit"):
        await call(client, "passerine_convert", {"file": "huge.mid"})

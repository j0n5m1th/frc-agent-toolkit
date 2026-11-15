"""Functional test of `mcp-ctre-passerine` (MIDI to CHRP): the installed command, over stdio.

Needs no network and no CTRE program. "passerine 1.0.0" is `fake_passerine.py` behind a tiny launcher; it behaves the way the real one was seen to (see
docs/CTRE.md), including silently overwriting an output, which the server must never let happen. Covers the tool list, listing, a real conversion through a
child process, the refusals (overwrite, a name that leaves the folder, a file that is not MIDI), and a run that reports success without writing a file.
"""

from pathlib import Path

from ctre_support import HERE, case, install_fake
from harness import Report, call, client

TOOLS = {"passerine_list", "passerine_convert"}
MIDI = b"MThd" + bytes(10)


async def run(keep: bool = False) -> bool:
    with case("ctre-passerine server", "ctre-passerine", keep) as (report, folder, env):
        music = folder / "music"
        (music / "song.mid").write_bytes(MIDI)
        (music / "fake.mid").write_bytes(b"RIFF....WAVE")
        (music / "old.chrp").write_bytes(b"x")
        async with client("mcp-ctre-passerine", env) as c:
            report.check("offers exactly the 2 documented tools", {t.name for t in await c.list_tools()} == TOOLS)

            missing = await call(c, "passerine_convert", {"file": "song.mid"})
            report.check("with no passerine installed, convert gives the install command",
                         "ctre_install tool=passerine" in missing.get("EXC", "") and not (music / "song.chrp").exists(), missing)
        install_fake(folder / "tools", "passerine", "1.0.0", HERE / "fake_passerine.py")
        await with_passerine(report, env, music)
    return report.summary()


async def with_passerine(report: Report, env: dict[str, str], music: Path) -> None:
    async with client("mcp-ctre-passerine", env) as c:
        listed = await call(c, "passerine_list")
        report.check("lists the MIDI files only", sorted(row[0] for row in listed.get("rows", [])) == ["fake.mid", "song.mid"], listed)
        chrp = await call(c, "passerine_list", {"kind": "chrp"})
        report.check("lists the CHRP files with kind=chrp", [row[0] for row in chrp.get("rows", [])] == ["old.chrp"], chrp)

        done = await call(c, "passerine_convert", {"file": "song.mid"})
        report.check("convert writes a new .chrp beside the MIDI file through a real child process",
                     done.get("file") == "song.chrp" and done.get("passerine") == "1.0.0" and (music / "song.chrp").stat().st_size == 60, done)

        again = await call(c, "passerine_convert", {"file": "song.mid"})
        report.check("a second conversion to the same name is refused (the real passerine would overwrite) and the first file is untouched",
                     "never overwritten" in again.get("EXC", "") and (music / "song.chrp").stat().st_size == 60, again)

        named = await call(c, "passerine_convert", {"file": "song.mid", "out": "intro"})
        report.check("a chosen output name is used", named.get("file") == "intro.chrp" and (music / "intro.chrp").is_file(), named)

        escape = await call(c, "passerine_convert", {"file": "song.mid", "out": "../outside"})
        report.check("the output name cannot leave the music folder", "plain file name" in escape.get("EXC", "") and not (music.parent / "outside.chrp").exists(), escape)

        notmidi = await call(c, "passerine_convert", {"file": "fake.mid"})
        report.check("a file that is not MIDI is refused and nothing is written", "is not a MIDI file" in notmidi.get("EXC", "") and not (music / "fake.chrp").exists(), notmidi)

        wrong = await call(c, "passerine_convert", {"file": "old.chrp"})
        report.check("only .mid and .midi files are converted", "Only .mid/.midi files" in wrong.get("EXC", ""), wrong)

    async with client("mcp-ctre-passerine", {**env, "FAKE_PASSERINE_MODE": "silent"}) as c:
        silent = await call(c, "passerine_convert", {"file": "song.mid", "out": "silent"})
        report.check("a run that reports success without writing a file is an error and leaves nothing behind",
                     "failed" in silent.get("EXC", "") and not (music / "silent.chrp").exists(), silent)

    report.check("no temporary files were left in the music folder", not [p for p in music.iterdir() if p.name.endswith((".part", ".tmp"))])

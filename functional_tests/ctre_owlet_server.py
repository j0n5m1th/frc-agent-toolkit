"""Functional test of `mcp-ctre-owlet` (Phoenix `.hoot` logs to `.wpilog`): the installed command, over stdio.

Needs no network and no CTRE program. "Owlet 26.3.0" is `fake_owlet.py` behind a tiny launcher; it behaves the way the real Owlet was seen to (see
docs/CTRE.md) and writes a real .wpilog. So this covers the tool list, the exact-level rule, signals and conversions through real child processes,
the guards against Owlet's quirks (a bad signal id, an existing output file, success without a file), and opening the result with the log server.
Running the real Owlet on a real log stays a manual check.
"""

from pathlib import Path

from ctre_support import case, install_fake_owlet, write_hoot
from harness import Report, call, client

OWLET = {"owlet_list", "owlet_info", "owlet_signals", "owlet_convert"}


async def run(keep: bool = False) -> bool:
    with case("ctre-owlet server", "ctre-owlet", keep) as (report, folder, env):
        write_hoot(folder / "hoots" / "match.hoot", 19)
        write_hoot(folder / "hoots" / "newer.hoot", 21)
        await owlet_without_owlet(report, env)
        install_fake_owlet(folder / "tools")
        await owlet_with_owlet(report, env, folder)
    return report.summary()


async def owlet_without_owlet(report: Report, env: dict[str, str]) -> None:
    async with client("mcp-ctre-owlet", env) as c:
        report.check("owlet server offers exactly the 4 documented tools", {t.name for t in await c.list_tools()} == OWLET)

        listed = await call(c, "owlet_list")
        levels = {row[0]: row[3] for row in listed.get("rows", [])}
        report.check("lists the hoot files with the compliancy read from each header", levels == {"match.hoot": 19, "newer.hoot": 21}, listed)

        info = await call(c, "owlet_info", {"file": "match.hoot"})
        report.check("with no Owlet installed, info gives the exact install command", "ctre_install tool=owlet compliancy=19" in info.get("EXC", ""), info)

        outside = await call(c, "owlet_info", {"file": str(Path(env["CTRE_INDEX_FILE"]))})
        report.check("a file that is not a .hoot is refused", "Only .hoot files" in outside.get("EXC", ""), outside)

        none = await call(c, "owlet_convert", {"file": "match.hoot"})
        report.check("convert will not start without a choice of signals", "Say which signals" in none.get("EXC", ""), none)


async def owlet_with_owlet(report: Report, env: dict[str, str], folder: Path) -> None:
    hoots = folder / "hoots"
    async with client("mcp-ctre-owlet", env) as c:
        info = await call(c, "owlet_info", {"file": "match.hoot"})
        report.check("info picks the installed Owlet of exactly the log's level and reads the Pro check",
                     info.get("compliancy") == 19 and str(info.get("owlet", "")).startswith("26.3.0") and info.get("pro") is True, info)

        wrong = await call(c, "owlet_info", {"file": "newer.hoot"})
        report.check("a log of another level is not given to the Owlet it does not match, and the install command is named",
                     "ctre_install tool=owlet compliancy=21" in wrong.get("EXC", ""), wrong)

        signals = await call(c, "owlet_signals", {"file": "match.hoot"})
        report.check("signals come back as [name, id] pairs read through a real child process (progress and padding handled)",
                     signals.get("total") == 5 and ["DriveState/Pose", "ff0005"] in signals.get("signals", []) and "groups" in signals, signals)
        velocity = await call(c, "owlet_signals", {"file": "match.hoot", "name_contains": "velocity"})
        report.check("signals can be filtered by name", velocity.get("matches") == 2, velocity)

        bad = await call(c, "owlet_convert", {"file": "match.hoot", "signals": ["zzz", "ff0005"]})
        report.check("an unknown signal id is refused up front (the real Owlet would export everything)",
                     "Not signals of this file" in bad.get("EXC", "") and not (hoots / "match.wpilog").exists(), bad)

        done = await call(c, "owlet_convert", {"file": "match.hoot", "name_contains": "TalonFX-0", "signals": ["ff0001"]})
        target = hoots / "match.wpilog"
        report.check("convert writes only the chosen signals to a new .wpilog beside the hoot file",
                     done.get("file") == "match.wpilog" and done.get("signals") == 3 and target.is_file() and target.stat().st_size > 0, done)

        again = await call(c, "owlet_convert", {"file": "match.hoot", "signals": ["ff0001"]})
        report.check("a second conversion to the same name is refused and the first file is untouched",
                     "never overwritten" in again.get("EXC", "") and target.is_file(), again)

        everything = await call(c, "owlet_convert", {"file": "match.hoot", "all_signals": True, "out": "everything"})
        report.check("all_signals is the only way to export every signal", everything.get("signals") == "all" and (hoots / "everything.wpilog").is_file(), everything)

        escape = await call(c, "owlet_convert", {"file": "match.hoot", "signals": ["ff0001"], "out": "../outside"})
        report.check("the output name cannot leave the hoot folder", "plain file name" in escape.get("EXC", "") and not (folder / "outside.wpilog").exists(), escape)

    async with client("mcp-wpilib-wpilog", {"WPILOG_DIR": str(hoots)}) as w:
        opened = await call(w, "wpilog_open", {"path": "match.wpilog"})
        report.check("the converted file opens in the log server with exactly the chosen signals",
                     opened.get("topics") == 3 and opened.get("updates") == 150, opened)
        topics = await call(w, "wpilog_topics")
        report.check("its topics are the Phoenix signal names", set(topics.get("topics", {})) == {
            "RobotEnable", "Phoenix6/drive/TalonFX-0/Velocity", "Phoenix6/drive/TalonFX-0/Position"}, topics)

    async with client("mcp-ctre-owlet", {**env, "FAKE_OWLET_MODE": "fail"}) as c:
        failed = await call(c, "owlet_convert", {"file": "match.hoot", "signals": ["ff0001"], "out": "failing"})
        report.check("an Owlet that reports success without writing a file is an error, and nothing is left behind",
                     "Owlet failed" in failed.get("EXC", "") and not list(hoots.glob("failing*")), failed)

    report.check("no temporary files were left in the tools or hoot folders",
                 not [p for root in (folder / "tools", hoots) for p in root.rglob("*") if p.name.endswith((".part", ".tmp"))])

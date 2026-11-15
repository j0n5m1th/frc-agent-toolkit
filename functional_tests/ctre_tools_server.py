"""Functional test of `mcp-ctre-tools` (the CTRE version manager): the installed command, over stdio.

By default it needs no network: the index is the copy bundled with the package (`CTRE_INDEX_FILE`) and the tools folder is temporary. It covers
the tool list (`ctre_install` is hidden until `CTRE_ALLOW_INSTALL=1`), the status and version answers, and the install tool's refusals (nothing written). Downloads need CTRE's
real https host, so they stay in the pytest suite, except under `--live`: that refreshes the index from CTRE's real website and runs `fill_gaps`
into a temporary folder (about one index request and three downloads, spaced a second apart, deleted afterwards).
"""

from pathlib import Path

from ctre_support import case
from harness import Report, call, client, is_unknown_tool, tool_names

TOOLS = {"ctre_status", "ctre_versions"}  # listed with the switch off; ctre_install is hidden until CTRE_ALLOW_INSTALL=1


async def run(keep: bool = False, live: bool = False) -> bool:
    with case("ctre-tools server", "ctre-tools", keep) as (report, folder, env):
        await offline_checks(report, env, folder)
        if live:
            await live_check(report, folder)
    return report.summary()


async def offline_checks(report: Report, env: dict[str, str], folder: Path) -> None:
    async with client("mcp-ctre-tools", env) as c:
        report.check("tools server offers the 2 read tools and hides ctre_install", await tool_names(c) == TOOLS)

        status = await call(c, "ctre_status", {"tool": "owlet"})
        owlet = (status.get("tools") or [{}])[0]
        report.check("status: nothing installed means Owlet is missing, with a version to install, and levels to fill",
                     owlet.get("status") == "missing" and owlet.get("recommended") and owlet.get("gaps") and status.get("index", {}).get("source") == "file", status)
        report.check("status: without a project it says how to set one", "CTRE_PROJECT_DIR" in status.get("hint", ""), status)

        versions = await call(c, "ctre_versions", {"tool": "owlet", "limit": 2})
        report.check("versions lists newest first with compliancy",
                     len(versions.get("versions", [])) == 2 and versions["versions"][0].get("compliancy") is not None, versions)

        for label, args in (("a version", {"tool": "owlet"}), ("a compliancy", {"tool": "owlet", "compliancy": 19}), ("fill_gaps", {"tool": "owlet", "fill_gaps": True})):
            hidden = await call(c, "ctre_install", args)
            report.check(f"install of {label} fails as an unknown tool while CTRE_ALLOW_INSTALL is off", is_unknown_tool(hidden, "ctre_install"), hidden)
        report.check("nothing was written to the tools folder", not any((folder / "tools").iterdir()))

    async with client("mcp-ctre-tools", {**env, "CTRE_ALLOW_INSTALL": "1"}) as c:
        report.check("CTRE_ALLOW_INSTALL=1 lists ctre_install too (3 tools)", await tool_names(c) == TOOLS | {"ctre_install"})
        # Refused by their own rules before anything is downloaded (the index is the bundled file; no network is used).
        for label, args, word in (("a version and a compliancy together", {"tool": "owlet", "version": "1.0.0", "compliancy": 19}, "not both"),
                                  ("fill_gaps with a version", {"tool": "owlet", "fill_gaps": True, "version": "1.0.0"}, "stands alone"),
                                  ("a version CTRE does not list", {"tool": "owlet", "version": "0.0.0-nope"}, "lists no")):
            refused = await call(c, "ctre_install", args)
            report.check(f"with the switch on, install of {label} is still refused", word in refused.get("EXC", ""), refused)
        report.check("nothing was written to the tools folder either", not any((folder / "tools").iterdir()))

        unknown = await call(c, "ctre_versions", {"tool": "nope"})
        report.check("an unknown tool names the ones that exist", "owlet" in unknown.get("EXC", ""), unknown)


async def live_check(report: Report, folder: Path) -> None:
    """Against CTRE's real website, into a temporary folder: one index request, a few spaced downloads, then nothing more to do."""
    live_tools = folder / "live-tools"
    live_tools.mkdir()
    env = {"CTRE_TOOLS_DIR": str(live_tools), "CTRE_ALLOW_INSTALL": "1", "CTRE_ADVANTAGESCOPE_DIR": "off", "CTRE_TUNERX_DIR": "off", "CTRE_INDEX_FILE": ""}
    async with client("mcp-ctre-tools", env) as c:
        status = await call(c, "ctre_status", {"tool": "owlet"})
        report.check("live: the index comes from CTRE's website", status.get("index", {}).get("source") == "network", status)
        filled = await call(c, "ctre_install", {"tool": "owlet", "fill_gaps": True})
        report.check("live: fill_gaps installs one Owlet per released level, hash-checked", bool(filled.get("installed")) and "EXC" not in filled, filled)
        files = [p for p in live_tools.rglob("owlet-*") if p.is_file()]
        report.check("live: the files are in the tools folder", len(files) == len(filled.get("installed", [])) and files, files)
        again = await call(c, "ctre_install", {"tool": "owlet", "fill_gaps": True})
        report.check("live: running it again downloads nothing", again.get("already_complete") is True, again)
        after = await call(c, "ctre_status", {"tool": "owlet", "refresh": True})
        report.check("live: a refresh asks CTRE whether the index changed (a saved copy answers)", after.get("index", {}).get("source") in ("cache", "network"), after)

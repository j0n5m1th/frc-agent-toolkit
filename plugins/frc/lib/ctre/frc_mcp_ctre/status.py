"""One report joining the index, what is installed and the robot project: which tools are missing, current, or have an update.
"Current" is decided by `choose.assess`. A version from another season (a 2027 alpha Owlet next to a 2026 project) is listed under
`other` and never counts as the tool for this project.
"""

from typing import Any

from . import catalog, choose, config
from .choose import compliancy_of
from .fetch import IndexResult, load_index
from .index import Index, ToolVersion
from .platform import platform_key
from .project import ProjectFacts, read_project
from .store import Installed, scan
from .versions import try_parse_version


def _newer(a: ToolVersion, b: str) -> bool:
    parsed_a, parsed_b = a.parsed, try_parse_version(b)
    return bool(parsed_a and parsed_b and parsed_b < parsed_a)


def reader_targets(index: Index, tool: str, have: list[Installed], platform: str) -> list[tuple[int, str, ToolVersion, Installed | None]]:
    """For a reader tool: each released channel's compliancy (newest first) where nothing is installed or a newer build exists, as
    (level, channel, newest build, what is installed at that level). AdvantageScope keeps one Owlet per level; these are the gaps."""
    targets = []
    levels = sorted({c.compliancy: c.name for c in index.channels if not c.prerelease}.items(), reverse=True)
    for level, channel in levels:
        newest = choose.for_compliancy(index, tool, level, platform)
        if newest is None:
            continue
        mine = next((i for i in have if compliancy_of(index, i) == level), None)  # `have` is newest first
        if mine is None or _newer(newest, mine.version):
            targets.append((level, channel, newest, mine))
    return targets


def reader_gaps(index: Index, tool: str, have: list[Installed], platform: str, limit: int = 6) -> list[str]:
    """`reader_targets` as short sentences for the status report."""
    return [f"C{level} ({channel}): " + (f"have {mine.version}, newest {newest.version}" if mine else f"missing, newest {newest.version}")
            for level, channel, newest, mine in reader_targets(index, tool, have, platform)][:limit]


def tool_row(index: Index, tool: str, have: list[Installed], fit: choose.Fit, facts: ProjectFacts | None, platform: str) -> dict[str, Any]:
    """The status of one tool as a report row: what to use, what to install, and why. The decisions are `choose.assess`'s."""
    refused = [i for i in have if not i.verified]  # found, but never run: see `unverified`
    have = [i for i in have if i.verified]
    verdict = choose.assess(index, tool, have, fit, facts, platform)
    recommended, use = verdict.recommended, verdict.current.version if verdict.current else None
    row: dict[str, Any] = {"tool": tool, "use": use, "recommended": recommended.version if recommended else None}
    if verdict.note:
        row["note"] = verdict.note
    if recommended is None:
        row["status"] = "no_build"
        row["note"] = choose.no_build_reason(tool, fit.channel, platform)
    elif use is None:
        row["status"] = "missing"
    elif _newer(recommended, use):
        row["status"] = "update"
    else:
        row["status"] = "ok"
    if refused:
        row["unverified"] = [f"{i.version} ({i.source}): {i.problem}" for i in refused[:3]]
        if row["status"] == "missing":
            row["status"] = "unverified"  # something is there, but it is not CTRE's file or it changed
    if catalog.info(tool).policy == "reader" and (gaps := reader_gaps(index, tool, have, platform)):
        row["gaps"] = gaps
    if verdict.other:
        row["other"] = [i.version for i in verdict.other][:3]  # installed, but for another season: not used for this project
    return row


def build_status(result: IndexResult, facts: ProjectFacts | None, have: dict[str, list[Installed]], platform: str, only: str | None = None) -> dict[str, Any]:
    index = result.index
    fit = choose.project_fit(index, facts)
    names = [index.tool_name(only)] if only else list(index.tools)
    out: dict[str, Any] = {
        "platform": platform,
        "channel": fit.channel,
        "channel_basis": fit.basis,
        "index": {"source": result.source},
        "tools": [tool_row(index, name, have.get(name, []), fit, facts, platform) for name in names],
    }
    if result.source in ("stale-cache", "snapshot") and result.age_s is not None:
        out["index"]["age_hours"] = round(result.age_s / 3600, 1)  # only when the age matters
    if result.note:
        out["index"]["note"] = result.note
    if facts is None:
        out["hint"] = "Open Claude Code in your robot project (from a clone, set CTRE_PROJECT_DIR) to match tools to its Phoenix version."
    else:
        project: dict[str, Any] = {"folder": str(facts.folder), "year": facts.year, "gradlerio": facts.gradlerio, "phoenix6": facts.phoenix6}
        if facts.phoenix5:
            project["phoenix5"] = facts.phoenix5
        newest = choose.for_channel(index, choose.PHOENIX_YARDSTICK, fit.channel) if choose.PHOENIX_YARDSTICK.lower() in (n.lower() for n in index.tools) else None
        if facts.phoenix6 and newest and _newer(newest, facts.phoenix6):
            project["phoenix6_newer"] = newest.version  # the Diagnostics Program's versions follow the Phoenix library's
        out["project"] = project
        if facts.notes:
            out["notes"] = facts.notes
    return out


PLAIN_ROW = {"tool", "use", "recommended", "status"}


def compact_status(report: dict[str, Any]) -> dict[str, Any]:
    """The report for an assistant: tools that are fine (`ok`, with nothing else to say) become one short string each in `ok`; every other tool keeps its full row."""
    rows = report["tools"]
    fine = [r for r in rows if r["status"] == "ok" and set(r) == PLAIN_ROW]
    if not fine:
        return report
    return {**report, "tools": [r for r in rows if r not in fine], "ok": [f"{r['tool']} {r['use']}" for r in fine]}


def current_status(refresh: bool = False, only: str | None = None, offline: bool = False) -> dict[str, Any]:
    """The status report for this computer right now: loads the index, scans installed tools (verifying each) and reads CTRE_PROJECT_DIR when set."""
    result = load_index(refresh=refresh, offline=offline)
    folder = config.project_dir()
    facts = read_project(folder) if folder else None
    return build_status(result, facts, scan(result.index.tools, result.index), platform_key(), only)

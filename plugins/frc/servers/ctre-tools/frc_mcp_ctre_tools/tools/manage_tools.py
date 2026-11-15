"""ctre_status, ctre_versions and ctre_install: see, choose and install CTRE tool versions."""

import asyncio
from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_core.toolset import gated_by
from frc_mcp_ctre import catalog, choose, config
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.fetch import download_tool, is_installed, load_index
from frc_mcp_ctre.platform import platform_key
from frc_mcp_ctre.project import read_project
from frc_mcp_ctre.status import compact_status, current_status, reader_targets
from frc_mcp_ctre.store import installed
from frc_mcp_wpilib.tooling.topic_params import Limit

from ..app import INSTALL, LOOKUP, Tool, mcp, tool_errors

MAX_BATCH = 4  # most downloads one fill_gaps call makes
Refresh = Annotated[bool, Field(description="Ask CTRE for a new index now")]


@mcp.tool(annotations={**LOOKUP, "title": "CTRE tools status"}, output_schema=None)
@tool_errors
async def ctre_status(tool: Annotated[str | None, Field(description="One tool only")] = None, refresh: Refresh = False) -> ToolResult:
    """CTRE tool status: version in use, version to install, status (ok/update/missing/unverified/no_build); Owlet also `gaps`. Tools that are fine are listed as short strings in `ok`. With CTRE_PROJECT_DIR: the project's Phoenix version and channel."""
    report = await asyncio.to_thread(current_status, refresh, tool)
    return reply(report if tool else compact_status(report))  # asking about one tool always gets its full row


@mcp.tool(annotations={**LOOKUP, "title": "CTRE tool versions"}, output_schema=None)
@tool_errors
async def ctre_versions(
    tool: Tool,
    channel: Annotated[str | None, Field(description="e.g. 2026")] = None,
    limit: Limit = 10,
) -> ToolResult:
    """Versions CTRE lists for a tool, newest first: compliancy (format level), channels, and flags `nobuild` (none for this computer) and `installed`."""
    result = await asyncio.to_thread(load_index)
    index, platform = result.index, platform_key()
    name = index.tool_name(tool)
    have = {i.version for i in (await asyncio.to_thread(installed, [name], index))[name]}
    versions = index.versions(name)
    if channel:
        low, high = index.compliancy_range(channel)
        versions = [v for v in versions if v.compliancy is not None and low <= v.compliancy <= high]
    rows = []
    for v in versions[:limit]:
        row: dict[str, object] = {"version": v.version, "compliancy": v.compliancy}
        if v.compliancy is not None and (names := index.channels_for(v.compliancy)):
            row["channels"] = names
        if v.download(platform) is None:
            row["nobuild"] = True  # flags appear only when true: most rows carry neither
        if v.version in have:
            row["installed"] = True
        rows.append(row)
    out: dict[str, object] = {"tool": name, "total": len(versions), "versions": rows}
    if len(versions) > limit:
        out["omitted"] = len(versions) - limit
    if result.note:
        out["note"] = result.note
    return reply(out)


@mcp.tool(annotations={**INSTALL, "title": "Install a CTRE tool"}, output_schema=None, tags=gated_by(config.INSTALL))
@tool_errors
async def ctre_install(
    tool: Tool,
    version: Annotated[str | None, Field(description="Default: the recommended one")] = None,
    compliancy: Annotated[int | None, Field(description="Newest build at exactly this level (what a log needs)")] = None,
    fill_gaps: Annotated[bool, Field(description="Owlet: every missing or behind level (ctre_status `gaps`)")] = False,
) -> ToolResult:
    """Download a CTRE tool into CTRE_TOOLS_DIR, hash-checked. Needs CTRE_ALLOW_INSTALL=1. Never overwrites your own files."""
    config.INSTALL.require()
    result = await asyncio.to_thread(load_index)
    index, platform = result.index, platform_key()
    name = index.tool_name(tool)
    if fill_gaps:
        if version or compliancy is not None:
            raise CtreError("fill_gaps stands alone: do not also give a version or a compliancy.")
        if catalog.info(name).policy != "reader":
            raise CtreError(f"fill_gaps is for tools matched to one compliancy each (Owlet); {name} is not one. Install a version instead.")
        have = (await asyncio.to_thread(installed, [name], index))[name]
        targets = reader_targets(index, name, have, platform)
        batch, rest = targets[:MAX_BATCH], targets[MAX_BATCH:]  # a few files per call keeps the load on CTRE's server small; ask again for the rest
        done = []
        for level, _channel, newest, _mine in batch:
            await asyncio.to_thread(download_tool, newest, platform)  # one at a time; a failure stops here and the ones before it stay installed
            done.append(f"C{level}: {newest.version}")
        out_fill: dict[str, object] = {"tool": name, "installed": done, "already_complete": not targets}
        if rest:
            out_fill["remaining"] = len(rest)
        return reply(out_fill)
    if version and compliancy is not None:
        raise CtreError("Give a version or a compliancy, not both.")
    if compliancy is not None:
        item = choose.for_compliancy(index, name, compliancy, platform)
        if item is None:
            raise CtreError(f"CTRE lists no {name} at compliancy {compliancy} for {platform}. Use ctre_versions to see the levels that exist.")
    elif version:
        item = index.find(name, version)
        if item is None:
            known = ", ".join(v.version for v in index.versions(name)[:6])
            raise CtreError(f"CTRE lists no {name} {version}. Newest versions: {known}.")
    else:
        folder = config.project_dir()
        facts = await asyncio.to_thread(read_project, folder) if folder else None
        item = choose.best_for(index, name, choose.project_fit(index, facts), facts, platform)
        if item is None:
            fit = choose.project_fit(index, facts)
            raise CtreError(f"{choose.no_build_reason(name, fit.channel, platform)} Use ctre_versions to see what exists.")
    already = await asyncio.to_thread(is_installed, item, platform)  # a verified copy in this package's folder; one elsewhere (AdvantageScope's) does not count
    await asyncio.to_thread(download_tool, item, platform)
    out: dict[str, object] = {"tool": name, "version": item.version, "already_installed": already}  # where it went is CTRE_TOOLS_DIR; ctre_status lists what is installed
    if item.prerelease:
        out["note"] = "This is a pre-release version."
    return reply(out)

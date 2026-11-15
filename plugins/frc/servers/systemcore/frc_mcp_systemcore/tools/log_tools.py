"""Log tools: list, download, prune, and read the journal."""

import asyncio
import time
from typing import Annotated, Any, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from .. import config, logs, ssh
from ..app import FETCH, FULL_ONLY_TAG, PRUNE, READ_ONLY, Host, mcp, tool_errors
from ..errors import SystemcoreError
from ..target import resolve

Kind = Annotated[Literal["wpilog", "hoot", "other"] | None, Field(description="File kind")]
MAX_LISTED = 40


@mcp.tool(annotations={**READ_ONLY, "title": "List Systemcore logs"}, output_schema=None)
@tool_errors
async def systemcore_logs(kind: Kind = None, limit: Annotated[int, Field(ge=1, le=200)] = MAX_LISTED, host: Host = None) -> ToolResult:
    """Log files on the Systemcore, newest first; `active` marks the one being written."""
    target = await resolve(host)
    async with ssh.session(target) as session:
        entries = await asyncio.to_thread(logs.list_entries, session)
    chosen = logs.newest_first([e for e in entries if kind is None or e.kind == kind])
    totals = {k: round(sum(e.size for e in entries if e.kind == k) / 1e6, 1) for k in ("wpilog", "hoot", "other")}
    out: dict[str, Any] = {"host": target, "total": len(chosen), "total_mb": totals, "files": [logs.row(e) for e in chosen[:limit]]}
    if len(chosen) > limit:
        out["omitted"] = len(chosen) - limit
    if any(e.named_at is None for e in chosen[:limit]):
        out["hint"] = "Files marked no_time_in_name have no date: the Systemcore clock is often wrong."
    return reply(out)


@mcp.tool(annotations={**FETCH, "title": "Download Systemcore logs"}, output_schema=None)
@tool_errors
async def systemcore_logs_download(
    paths: Annotated[list[str] | None, Field(description="Paths from systemcore_logs")] = None,
    newest: Annotated[int | None, Field(ge=1, le=50, description="Instead of paths: the newest N of `kind`")] = None,
    kind: Kind = None,
    host: Host = None,
) -> ToolResult:
    """Copy log files to the download folder (a partial copy is resumed)."""
    if not paths and not newest:
        raise SystemcoreError("Give paths, or newest (with kind).")
    target = await resolve(host)
    async with ssh.session(target) as session:
        entries = await asyncio.to_thread(logs.list_entries, session)
        saved = await asyncio.to_thread(logs.download, session, target, entries, paths=paths, newest=newest, kind=kind)
    return reply({"host": target, "files": saved, "folder": str(config.download_dir() / target)})


@mcp.tool(annotations={**PRUNE, "title": "Prune Systemcore logs"}, output_schema=None)
@tool_errors
async def systemcore_logs_prune(
    keep_newest: Annotated[int | None, Field(ge=1, le=1000, description="Keep the newest N of each kind")] = None,
    max_total_gb: Annotated[float | None, Field(gt=0, le=100, description="Keep the newest files up to this size, per kind")] = None,
    older_than_days: Annotated[float | None, Field(gt=0, le=3650, description="Delete files older than this")] = None,
    kind: Kind = None,
    only_downloaded: Annotated[bool, Field(description="Only files already downloaded here")] = True,
    dry_run: Annotated[bool, Field(description="Delete nothing; list")] = True,
    expect_count: Annotated[int | None, Field(ge=0, description="Count the dry run showed, to delete")] = None,
    host: Host = None,
) -> ToolResult:
    """Delete old logs by rule (any rule marks a file). The newest of each kind and any file being written stay. Dry run unless dry_run=false (needs SYSTEMCORE_ALLOW_PRUNE=1 and expect_count)."""
    if keep_newest is None and max_total_gb is None and older_than_days is None:
        raise SystemcoreError("Give at least one rule: keep_newest, max_total_gb or older_than_days.")
    if not dry_run:
        config.prune.require()
        if expect_count is None:
            raise SystemcoreError("Run it as a dry run first, show the user the list, then pass expect_count with the number shown.")
    target = await resolve(host)
    async with ssh.session(target) as session:
        entries = await asyncio.to_thread(logs.list_entries, session)
        chosen, protected = logs.plan_prune(entries, target, kind=kind, keep_newest=keep_newest, max_total_gb=max_total_gb,
                                            older_than_days=older_than_days, only_downloaded=only_downloaded, now=time.time())
        out: dict[str, Any] = {"host": target, "dry_run": dry_run, "count": len(chosen), "mb": round(sum(e.size for e in chosen) / 1e6, 1),
                               "files": [logs.row(e) for e in logs.newest_first(chosen)[:MAX_LISTED]]}
        if only_downloaded and protected:
            out["kept_not_downloaded"] = len(protected & {e.path for e in entries if e.kind == (kind or e.kind)})
        if dry_run:
            out["hint"] = f"Nothing deleted. To delete: dry_run=false, expect_count={len(chosen)}."
            return reply(out)
        if expect_count != len(chosen):
            raise SystemcoreError(f"The rules now match {len(chosen)} files, not {expect_count}. Nothing was deleted; run the dry run again.")
        deleted, failed = await asyncio.to_thread(logs.delete, session, chosen)
    out.update(deleted=deleted, failed=failed)
    return reply(out)


@mcp.tool(annotations={**READ_ONLY, "title": "Systemcore journal"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def systemcore_journal(
    lines: Annotated[int, Field(ge=1, le=300)] = 60,
    service: Annotated[str | None, Field(description="systemd unit, e.g. robot")] = None,
    priority: Annotated[Literal["err", "warning", "notice", "info"] | None, Field(description="This level and worse")] = None,
    boot: Annotated[int, Field(le=0, ge=-5, description="0 = this boot, -1 = previous")] = 0,
    host: Host = None,
) -> ToolResult:
    """Robot console output: last lines of the journal."""
    command = logs.journal_command(lines, service, priority, boot)
    target = await resolve(host)
    async with ssh.session(target) as session:
        code, text, err = await asyncio.to_thread(session.run, command, 30)
    if code != 0:
        raise SystemcoreError(f"journalctl failed: {err.strip()[:200]}")
    return reply({"host": target, "lines": [line[:300] for line in text.splitlines()]})

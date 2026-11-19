"""`nt4_health`: a Systemcore's own health topics (port 6810) read and judged in one call."""

from fastmcp.tools import ToolResult

from frc_mcp_core.reply import reply

from ..access.reading import Observed
from ..access.topic_listing import TopicFilter
from ..app import READ_ONLY, mcp, tool_errors
from ..health import ALERTS, MAX_ALERTS, MAX_TOPICS, PREFIXES, active_alerts, assess
from ..session import session

READ_WAIT_S = 2.0


@mcp.tool(annotations={**READ_ONLY, "title": "Controller health"}, output_schema=None)
@tool_errors
async def nt4_health() -> ToolResult:
    """Systemcore health in one call (connect with nt4_connection port=6810 first): battery, brownout, CPU, memory, storage, CAN, robot code, alerts. `flags` rank high to low; says what it could not check."""
    async with session.operation():
        names: list[str] = []
        for prefix in PREFIXES:
            found = session.list_topics(TopicFilter(prefix=prefix)).topics
            names += [t["name"] for t in found if prefix != ALERTS or t["name"].endswith("/active")]  # an alert's text is read only when it is on
        got = await session.get_values(names[:MAX_TOPICS], timeout_s=READ_WAIT_S)
        values = {name: seen.plain for name, seen in got.items() if isinstance(seen, Observed)}
        texts: dict[str, str] = {}
        if bases := active_alerts(values)[:MAX_ALERTS]:
            read = await session.get_values([base + "/text" for base in bases], timeout_s=READ_WAIT_S)
            texts = {name.removesuffix("/text"): str(seen.plain) for name, seen in read.items() if isinstance(seen, Observed)}
        out = assess(values, texts)
        if (cut := len(names) - MAX_TOPICS) > 0:
            out["topics_not_read"] = cut
            out["flags"] = [*(f for f in out["flags"] if not f.startswith("none found")), f"low: {cut} topics were not read (limit {MAX_TOPICS}); the checks above may be incomplete"]
        return reply(out)

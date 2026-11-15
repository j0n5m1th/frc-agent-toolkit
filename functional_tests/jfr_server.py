"""Functional test of `mcp-jfr`: the installed command, over stdio, against a folder of recordings.

The reading tools parse a `.jfr` with the JDK's `jfr` binary, so those stay in the pytest suite (which uses the same
JDK); what needs proving over stdio is the bootstrap: the tool list, the prompt, and the two answers that need no JDK.
"""

import shutil

from harness import OUTPUT, Report, call, client, is_unknown_tool, tool_names

GATED_TOOLS = {"jfr_record"}  # hidden until JFR_ALLOW_RECORD=1
FULL_TOOLS = {"jfr_around", "jfr_compare", "jfr_hotspots", "jfr_list", "jfr_live", "jfr_memory", "jfr_summary"}  # what is listed with the switch off
CORE_TOOLS = FULL_TOOLS - {"jfr_live"}
JFR_BYTES = b"\x00\x01\x02\x03"  # `jfr_list` only stats the file, so the four bytes of a header are enough


async def run(keep: bool = False) -> bool:
    report = Report("jfr server")
    folder = OUTPUT / "jfr"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "demo.jfr").write_bytes(JFR_BYTES)
    env = {"JFR_DIR": str(folder)}
    try:
        async with client("mcp-jfr", env) as c:
            report.check("offers the 7 tools that are always there, and hides jfr_record", await tool_names(c) == FULL_TOOLS)

            report.check("offers the one prompt", {p.name for p in await c.list_prompts()} == {"find_hot_spots"})

            listed = await call(c, "jfr_list", {"kind": "recordings"})
            report.check("lists the recording in JFR_DIR", listed.get("total") == 1
                         and [r["file"] for r in listed.get("recordings", [])] == ["demo.jfr"], listed)

            filtered = await call(c, "jfr_list", {"kind": "recordings", "name_contains": "zzz"})
            report.check("name_contains filters it out", filtered.get("total") == 0, filtered)

            missing = await call(c, "jfr_summary", {"file": "nope.jfr"})
            report.check("an unknown recording is an error, not a crash", "EXC" in missing, missing)

            hidden = await call(c, "jfr_record", {"pid": 1, "seconds": 1})
            report.check("jfr_record is not a tool while JFR_ALLOW_RECORD is off", is_unknown_tool(hidden, "jfr_record"), hidden)

        async with client("mcp-jfr", {**env, "JFR_ALLOW_RECORD": "1"}) as c:
            report.check("JFR_ALLOW_RECORD=1 lists jfr_record too (8 tools)", await tool_names(c) == FULL_TOOLS | GATED_TOOLS)
            too_long = await call(c, "jfr_record", {"pid": 1, "seconds": 10**6})  # refused by JFR_MAX_SECONDS before any JVM tool runs
            report.check("a recording longer than JFR_MAX_SECONDS is refused, nothing runs",
                         "JFR_MAX_SECONDS" in str(too_long.get("EXC", "")), too_long)

        async with client("mcp-jfr", {**env, "JFR_TOOLSET": "core"}) as c:
            report.check("JFR_TOOLSET=core drops jfr_live (and jfr_record is still hidden)", await tool_names(c) == CORE_TOOLS)
            report.check("the core set still lists recordings", (await call(c, "jfr_list")).get("total") == 1)
    finally:
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)
    return report.summary()

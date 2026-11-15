"""Functional test of `mcp-wpilib-wpilog`: the installed command, over stdio, against the deterministic practice log."""

import shutil

from fake_log import (
    build,  # the evaluation's practice-log writer, so expected values match it
)
from harness import OUTPUT, Report, call, client

TOOLS = {"wpilog_close", "wpilog_get_values", "wpilog_list", "wpilog_open", "wpilog_query", "wpilog_text",
         "wpilog_topics", "wpilog_when", "wpilog_diff", "wpilog_sync"}


async def run(keep: bool = False) -> bool:
    report = Report("wpilog server")
    folder = OUTPUT / "wpilog"
    shutil.rmtree(folder, ignore_errors=True)
    build().save(folder / "practice.wpilog")
    build(60.0).save(folder / "practice60.wpilog")  # the same match, twice as long: its settings are the same
    try:
        async with client("mcp-wpilib-wpilog", {"WPILOG_DIR": str(folder)}) as c:
            report.check("offers exactly the 10 documented tools", {t.name for t in await c.list_tools()} == TOOLS)

            report.check("offers the three prompts", {p.name for p in await c.list_prompts()} == {"review_match", "find_problems", "scan_match"})

            listing = await call(c, "wpilog_list")
            report.check("lists both practice logs", {entry["file"] for entry in listing.get("logs", [])} == {"practice.wpilog", "practice60.wpilog"}, listing)

            opened = await call(c, "wpilog_open", {"path": "practice.wpilog"})
            report.check("opens it as l1, 30 s long", opened.get("log") == "l1" and opened.get("duration_s") == 30.0, opened)
            report.check("finds the auto and teleop phases", [p["mode"] for p in opened.get("enabled", [])] == ["auto", "teleop"], opened)

            topics = await call(c, "wpilog_topics", {"counts": True})
            report.check("lists 15 topics and hides the schema internals", topics.get("total") == 15 and topics.get("hidden_matches", 0) > 0, topics)

            values = await call(c, "wpilog_get_values", {
                "topics": ["/Fake/Arm/Angle", "/Fake/Drive/Pose"], "at_ms": 2500})
            report.check("value at 2.5 s: arm angle is 30", abs(values["/Fake/Arm/Angle"]["v"] - 30.0) < 0.5, values)
            report.check("a struct topic decodes to an object", isinstance(values["/Fake/Drive/Pose"]["v"], dict), values)

            battery = await call(c, "wpilog_query", {"topics": ["/Fake/Battery/Voltage"], "reduce": "summary"})
            report.check("lowest battery voltage is 11.475", abs(battery["data"]["/Fake/Battery/Voltage"]["min"] - 11.475) < 1e-6, battery)

            spikes = await call(c, "wpilog_when", {"topic": "/Fake/Elevator/Current", "op": ">", "value": 40,
                                                   "align": "auto", "since_ms": 0, "until_ms": 15000})
            report.check("3 elevator spikes above 40 A during autonomous", spikes.get("n") == 3, spikes)

            joint = await call(c, "wpilog_when", {"topic": "/Fake/Elevator/Current", "op": ">", "value": 40, "align": "auto",
                                                  "also": [{"topic": "/Fake/Match/Mode", "op": "==", "value": "auto"}]})
            report.check("`also`: spikes while in autonomous, at 3 s, 8 s and 13 s in",
                         [i[0] for i in joint.get("intervals_ms", [])] == [3000.0, 8000.0, 13000.0], joint)

            error = await call(c, "wpilog_text", {"name_contains": "ERROR"})
            report.check("the ERROR message is at 22 s", error.get("n") == 1 and error["items"][0]["t_ms"] == 22000.0, error)

            changes = await call(c, "wpilog_when", {"topic": "/Fake/Match/Mode", "op": "changed"})
            report.check("op=changed lists the mode changes", "EXC" not in changes and changes.get("n", 0) > 0, changes)

            missing = await call(c, "wpilog_get_values", {"topics": ["/Nope"]})
            report.check("an unknown topic is an error entry, not a crash", "EXC" not in missing, missing)

            again = await call(c, "wpilog_open", {"path": "practice.wpilog"})
            report.check("reopening the same file reuses its id", again.get("log") == "l1", again)

            outside = await call(c, "wpilog_open", {"path": "../nope.wpilog"})
            report.check("a path outside the allowed folder is refused", "EXC" in outside, outside)

            second = await call(c, "wpilog_open", {"path": "practice60.wpilog"})
            diff = await call(c, "wpilog_diff", {"other": "l1"})
            report.check("`wpilog_diff` finds the settings of two practice logs identical", second.get("log") == "l2" and
                         diff.get("same", 0) > 0 and "changed" not in diff and "only_in" not in diff, diff)

            closed = await call(c, "wpilog_close")
            report.check("closes the log", "EXC" not in closed, closed)
    finally:
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)
    return report.summary()

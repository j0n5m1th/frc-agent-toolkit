"""Prompt templates: a ready-made profiling pass a user can pick in their assistant."""

from .app import mcp


@mcp.prompt
def find_hot_spots(match: str = "", seconds: int = 90) -> str:
    """Record a running JVM and report the biggest time and memory costs."""
    which = f" whose command line contains {match!r}" if match else ""
    return (
        f"Find the JVM{which} with jfr_list(kind='jvms') (ask me if several match), record it for {seconds} seconds with jfr_record, then "
        "jfr_summary. Rank hot spots with jfr_hotspots by total and by max (use the custom event types the summary shows, "
        "else jdk.ExecutionSample), and for each worst-case outlier run jfr_around before blaming the code. Check jfr_memory view=gc and "
        "view=alloc too. Report numbers, say what the recording cannot show, and do not edit code until asked."
    )

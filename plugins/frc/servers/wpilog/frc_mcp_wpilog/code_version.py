"""Which robot code made a log: the build facts AdvantageKit (and teams' own build scripts) log as text, such as `/RealMetadata/GitSHA`.

A student can then tell which version of the code a match ran, and look at that version when a topic's meaning is unclear. The key names are
AdvantageKit's `BuildConstants`.
"""

from frc_mcp_wpilib.scan_roles import leaf_and_parents

from .loglike import LogLike

KEYS = {key.lower(): key for key in ("GitSHA", "GitBranch", "GitDirty", "GitDate", "BuildDate", "ProjectName", "Version")}
MAX_CHARS = 80


def code_version(log: LogLike) -> tuple[dict[str, str], list[str]]:
    """`({key: value}, warnings)`: the first value of each build-fact topic. A key logged under two paths (`/RealMetadata`, `/ReplayMetadata`)
    takes the first topic the log declared, and a warning names the paths when their values differ. `Version` counts only under a `metadata` path."""
    keys: dict[str, str] = {}  # topic name -> key, in declaration order
    for topic in log.topics.values():
        leaf, parents = leaf_and_parents(topic.name)
        if topic.type_str == "string" and not topic.hidden and leaf in KEYS and (leaf != "version" or any("metadata" in p for p in parents)):
            keys[topic.name] = KEYS[leaf]
    loaded = log.rows(list(keys))
    found: dict[str, list[tuple[str, str]]] = {}  # key -> [(topic, value)]
    for name, key in keys.items():
        if rows := loaded[name]:
            found.setdefault(key, []).append((name, str(rows[0][1])[:MAX_CHARS]))
    warnings = []
    for key, values in found.items():
        if len({value for _, value in values}) > 1:
            warnings.append(f"{key} differs between {' and '.join(topic for topic, _ in values)}; the first is shown.")
    return {key: values[0][1] for key, values in found.items()}, warnings

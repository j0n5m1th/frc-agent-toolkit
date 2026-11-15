"""Choose a tool version for a release channel, a robot project, or a log file (policies: `catalog`; our reading of CTRE's index).
- A channel covers the compliancy levels from its season's first beta/alpha up to its own level (`2026` covers 18 to 19).
- A pre-release tool version is chosen only for a pre-release channel.
- A tool with no compliancy (caniv) cannot be placed on a channel: the newest release is used.
- A log needs a tool of EXACTLY its compliancy, the newest build at that level. Seen on a real compliancy 19 log: Owlet 21 and 20
  refused it, 19 read it, and 18 exited 0 but listed 36 signals instead of 2,124. There is no "close enough" level.
"""

from dataclasses import dataclass

from . import catalog
from .errors import CtreError
from .index import Index, ToolVersion, newest_first
from .project import ProjectFacts
from .store import Installed
from .versions import try_parse_version

PHOENIX_YARDSTICK = "PhoenixDiagnosticsProgram"  # its versions follow the Phoenix library's, one for one (seen: 26.1.3 in both)


@dataclass(frozen=True)
class Fit:
    """Which release channel a project is on, and how sure we are."""

    channel: str
    compliancy: int
    exact: bool  # True when the project's Phoenix version was found in the index; False when only its season was used
    basis: str


def _buildable(items: list[ToolVersion], platform: str | None) -> list[ToolVersion]:
    return [v for v in items if platform is None or v.download(platform)]


def for_channel(index: Index, tool: str, channel: str, platform: str | None = None) -> ToolVersion | None:
    """The newest version of `tool` that belongs to `channel` (and has a build for `platform`, when given)."""
    chosen = index.channel(channel)
    low, high = index.compliancy_range(channel)
    versions = _buildable(index.versions(tool), platform)
    if all(v.compliancy is None for v in versions):
        releases = [v for v in versions if not v.prerelease]
        return (releases or versions)[0] if versions else None
    fits = [v for v in versions if v.compliancy is not None and low <= v.compliancy <= high and (chosen.prerelease or not v.prerelease)]
    return fits[0] if fits else None


def for_compliancy(index: Index, tool: str, compliancy: int, platform: str | None = None) -> ToolVersion | None:
    """The newest version of `tool` at exactly this compliancy (a release if there is one); None when CTRE lists none."""
    same_level = [v for v in _buildable(index.versions(tool), platform) if v.compliancy == compliancy]
    return newest_first([v for v in same_level if not v.prerelease] or same_level)[0] if same_level else None


def project_fit(index: Index, facts: ProjectFacts | None) -> Fit:
    """The channel to use for a project. Without a project (or without Phoenix in it) that is CTRE's latest channel."""
    if facts is not None and facts.phoenix6:
        found = index.find(PHOENIX_YARDSTICK, facts.phoenix6) if PHOENIX_YARDSTICK.lower() in (n.lower() for n in index.tools) else None
        if found is not None and found.compliancy is not None:
            names = index.channels_for(found.compliancy)
            if names:
                chosen = sorted(names, key=lambda n: index.channel(n).prerelease)[0]
                return Fit(chosen, found.compliancy, True, f"Phoenix {facts.phoenix6} is a listed release (compliancy {found.compliancy})")
        parsed = try_parse_version(facts.phoenix6)
        if parsed and parsed.season:
            return _season_fit(index, parsed.season, f"Phoenix {facts.phoenix6} is not in CTRE's list; using its season ({parsed.season})")
    if facts is not None and facts.year and facts.year.isdigit():
        return _season_fit(index, int(facts.year), f"no Phoenix vendordep; using the project year ({facts.year})")
    latest = index.channel(index.latest_channel)
    return Fit(latest.name, latest.compliancy, False, "no project given; using CTRE's latest channel")


def _season_fit(index: Index, season: int, basis: str) -> Fit:
    channels = [c for c in index.channels if c.season == season]
    if not channels:
        latest = index.channel(index.latest_channel)
        return Fit(latest.name, latest.compliancy, False, f"{basis}, but CTRE has no {season} channel; using the latest")
    best = sorted(channels, key=lambda c: (c.prerelease, -c.compliancy))[0]
    return Fit(best.name, best.compliancy, False, basis)


def best_for(index: Index, tool: str, fit: Fit, facts: ProjectFacts | None, platform: str | None = None) -> ToolVersion | None:
    """The version of `tool` to recommend for a project fit, following the tool's policy (`match` prefers the project's own Phoenix version)."""
    policy = catalog.info(tool).policy
    if policy == "reader":
        return for_compliancy(index, tool, fit.compliancy, platform)
    if policy == "match" and facts is not None and facts.phoenix6 and fit.exact:
        exact = index.find(tool, facts.phoenix6)
        if exact is not None and (platform is None or exact.download(platform)):
            return exact
    chosen = for_channel(index, tool, fit.channel, platform)
    if chosen is None and policy == "standalone":
        return newest_release(index, tool, platform)  # no pairing is known for this tool: a build from another channel beats none
    return chosen


def compliancy_of(index: Index, item: Installed) -> int | None:
    """An installed version's compliancy: from its file name when it has one, else from the index."""
    if item.compliancy is not None:
        return item.compliancy
    listed = index.find(item.tool, item.version)
    return listed.compliancy if listed else None


@dataclass(frozen=True)
class Assessment:
    """What to say about one tool for one project: the version to install, the installed copy that counts, the ones that do not, and a caveat."""

    recommended: ToolVersion | None
    current: Installed | None
    other: list[Installed]  # installed, but of another season: never the tool for this project
    note: str | None = None


def assess(index: Index, tool: str, have: list[Installed], fit: Fit, facts: ProjectFacts | None, platform: str | None) -> Assessment:
    """The one place a tool's policy decides what is recommended and what installed copy counts (see `catalog`); `have` is newest first.

    reader: only the exact compliancy of the project counts. match: the project's channel range. standalone: no pairing is known, so any installed copy counts
    and the newest release of any channel may be recommended (with a note when it is not from the project's channel).
    """
    policy = catalog.info(tool).policy
    recommended = best_for(index, tool, fit, facts, platform)
    low, high = index.compliancy_range(fit.channel)
    if policy == "standalone":
        note = None
        if recommended is not None and recommended.compliancy is not None and not low <= recommended.compliancy <= high:
            names = ", ".join(index.channels_for(recommended.compliancy)) or "another"
            note = f"Not in channel {fit.channel}: the newest {tool} is from channel {names}; whether it fits this project is not checked."
        return Assessment(recommended, have[0] if have else None, [], note)
    if policy == "reader":
        low = high = fit.compliancy
    paired = any(v.compliancy is not None for v in index.versions(tool))  # a tool with no compliancy at all cannot be placed on a channel
    inside = [i for i in have if not paired or (level := compliancy_of(index, i)) is not None and low <= level <= high]
    return Assessment(recommended, inside[0] if inside else None, [i for i in have if i not in inside])


def no_build_reason(tool: str, channel: str, platform: str) -> str:
    """Why a tool cannot be recommended here: CTRE's own statement for the platform when it made one, else that CTRE lists nothing for the channel."""
    return catalog.platform_note(tool, platform) or f"CTRE lists no {tool} for channel {channel} on {platform}."


def newest_release(index: Index, tool: str, platform: str | None = None) -> ToolVersion | None:
    """The newest release (not a pre-release) of `tool` with a build for `platform`, whatever its channel or compliancy; a pre-release only if there is no release."""
    versions = _buildable(index.versions(tool), platform)
    if not versions:
        return None
    return ([v for v in versions if not v.prerelease] or versions)[0]


def require(item: ToolVersion | None, what: str) -> ToolVersion:
    if item is None:
        raise CtreError(f"CTRE's index has no {what}.")
    return item

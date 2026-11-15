"""CTRE's tool index (https://redist.ctr-electronics.com/index.json) as plain Python.
Shape, as seen in the snapshot this package ships: `{"JsonVersion", "LatestChannel", "ChannelCompliancy": [{"Name", "Compliancy"}],
"Tools": [{"Name", "Items": [{"Version", "Compliancy", "Urls": {"<platform>": url, "<platform>-md5": hash, "<platform>-sha1": hash}}]}]}`.
Compliancy is a whole number CTRE raises when a log or device format changes; a tool version's can be null (caniv, an old
passerine). Channels are release lines (`2026`, `2026-beta-1`, `2027-alpha-7`), each with a compliancy.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import CtreError
from .versions import Version, try_parse_version

_log = logging.getLogger(__name__)
_HASH_SUFFIXES = ("-md5", "-sha1")


@dataclass(frozen=True)
class Download:
    url: str
    md5: str | None
    sha1: str | None


_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def safe_version(version: str, tool: str) -> str:
    """`version` if it can be a folder name (no separators, `..`, `:`); else CtreError. The index is outside data: the version becomes a path part."""
    if not _SAFE_VERSION.match(version) or ".." in version:
        raise CtreError(f"CTRE's index gives an unusable version for {tool}: {version[:40]!r}.")
    return version


@dataclass(frozen=True)
class ToolVersion:
    tool: str
    version: str
    compliancy: int | None
    urls: dict[str, str] = field(default_factory=dict, compare=False, hash=False)

    @property
    def parsed(self) -> Version | None:
        return try_parse_version(self.version)

    @property
    def prerelease(self) -> bool:
        parsed = self.parsed
        return bool(parsed and parsed.prerelease)

    def platforms(self) -> list[str]:
        return sorted(k for k in self.urls if not k.endswith(_HASH_SUFFIXES))

    def download(self, platform: str) -> Download | None:
        """The url and published hashes for one platform; None when CTRE built nothing for it."""
        if (url := self.urls.get(platform)) is None:
            return None
        return Download(url, self.urls.get(f"{platform}-md5"), self.urls.get(f"{platform}-sha1"))


@dataclass(frozen=True)
class Channel:
    name: str
    compliancy: int

    @property
    def season(self) -> int | None:
        match = re.match(r"^(\d{4})(?:-|$)", self.name)
        return int(match.group(1)) if match else None

    @property
    def prerelease(self) -> bool:
        return self.name != str(self.season)


def newest_first(items: "list[ToolVersion] | tuple[ToolVersion, ...]") -> list[ToolVersion]:
    """Sort versions newest first; a version string that does not parse goes last."""
    parsed = sorted((v for v in items if v.parsed), key=lambda v: v.parsed.sort_key(), reverse=True)  # type: ignore[union-attr]
    return parsed + [v for v in items if not v.parsed]


@dataclass(frozen=True)
class Index:
    json_version: str
    latest_channel: str
    channels: tuple[Channel, ...]
    tools: dict[str, tuple[ToolVersion, ...]]
    skipped_versions: int = 0  # entries left out because their version text cannot be a folder name

    def tool_name(self, tool: str) -> str:
        """The index's own spelling of a tool name (matching ignores case)."""
        for name in self.tools:
            if name.lower() == tool.lower():
                return name
        raise CtreError(f"CTRE's index has no tool {tool!r}. It lists: {', '.join(self.tools)}.")

    def versions(self, tool: str) -> list[ToolVersion]:
        """Every version of a tool, newest first."""
        return newest_first(self.tools[self.tool_name(tool)])

    def find(self, tool: str, version: str) -> ToolVersion | None:
        return next((v for v in self.versions(tool) if v.version == version), None)

    def channel(self, name: str) -> Channel:
        for channel in self.channels:
            if channel.name == name:
                return channel
        raise CtreError(f"CTRE has no channel {name!r}. Channels: {', '.join(c.name for c in self.channels)}.")

    def compliancy_range(self, channel: str) -> tuple[int, int]:
        """The compliancy levels a channel's season covers: from the season's first beta (or alpha) up to the channel itself."""
        chosen = self.channel(channel)
        same_season = [c.compliancy for c in self.channels if c.season == chosen.season]
        return min(same_season), chosen.compliancy

    def channels_for(self, compliancy: int) -> list[str]:
        return [c.name for c in self.channels if c.compliancy == compliancy]


def _need(data: Any, key: str, kind: type, where: str) -> Any:
    if not isinstance(data, dict) or not isinstance(data.get(key), kind):
        raise CtreError(f"CTRE's tool index looks different than expected: {where} has no {key!r} of type {kind.__name__}.")
    return data[key]


def parse_index(raw: str | bytes) -> Index:
    """Parse the index's JSON. Anything missing or mistyped is a CtreError saying where, so a format change is noticed early."""
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise CtreError(f"CTRE's tool index is not valid JSON: {e}") from e
    channels = tuple(Channel(_need(c, "Name", str, "a channel"), _need(c, "Compliancy", int, "a channel")) for c in _need(data, "ChannelCompliancy", list, "the index"))
    tools: dict[str, tuple[ToolVersion, ...]] = {}
    skipped = 0
    for tool in _need(data, "Tools", list, "the index"):
        name = _need(tool, "Name", str, "a tool")
        items = []
        for item in _need(tool, "Items", list, f"tool {name}"):
            compliancy = item.get("Compliancy") if isinstance(item, dict) else None
            urls = _need(item, "Urls", dict, f"a version of {name}")
            version = _need(item, "Version", str, f"a version of {name}")
            try:
                safe_version(version, name)
            except CtreError as e:
                skipped += 1  # one odd version must not cost every CTRE server the fresh index
                _log.warning("CTRE's index: %s It is left out (%d so far).", e, skipped)
                continue
            items.append(ToolVersion(name, version, compliancy if isinstance(compliancy, int) else None,
                                     {k: v for k, v in urls.items() if isinstance(k, str) and isinstance(v, str)}))
        tools[name] = tuple(items)
    return Index(str(data.get("JsonVersion", "")), _need(data, "LatestChannel", str, "the index"), channels, tools, skipped)

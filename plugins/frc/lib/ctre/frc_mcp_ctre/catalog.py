"""What each CTRE tool is for, and how to choose a version of it.
Tools live at `<tools folder>/<folder>/<version>/<name>-<version>-<platform>[.exe]` (seen on a real Windows machine: an existing
`%LOCALAPPDATA%/ctre` had this shape). The folder is the lower-case tool name, except `PhoenixDiagnosticsProgram` (`phoenix-diag`).
Policies (our reading of the index, not a CTRE statement):
- `reader`: reads files other CTRE software wrote (Owlet, `.hoot`); needs the tool whose compliancy EXACTLY equals the file's.
  Seen: on a compliancy 19 log, Owlet 20 and 21 refused it and Owlet 18 silently listed almost no signals.
- `match`: talks to devices or the robot library; use the version that matches the project's Phoenix version.
- `standalone`: no pairing known; the newest release of the channel.
"""

from dataclasses import dataclass, field
from typing import Literal

Policy = Literal["reader", "match", "standalone"]


@dataclass(frozen=True)
class ToolInfo:
    name: str  # the index's spelling
    folder: str
    purpose: str
    policy: Policy
    not_on: dict[str, str] = field(default_factory=dict)  # platform key -> why CTRE has no build for it (their statement, not ours)


CATALOG: tuple[ToolInfo, ...] = (
    ToolInfo("owlet", "owlet", "turns Phoenix .hoot logs into .wpilog (or mcap) files", "reader"),
    ToolInfo("PhoenixDiagnosticsProgram", "phoenix-diag", "Phoenix Diagnostics Program: look at and configure CTRE devices", "match"),
    ToolInfo("caniv", "caniv", "lists, inspects and configures CANivore devices", "standalone",
             {"macosuniversal": "CTRE's documentation says caniv is not available on macOS (docs.ctr-electronics.com/cli-tools); the index lists no macOS build either."}),
    ToolInfo("corvus", "corvus", "CTRE tool (purpose not documented here yet)", "standalone"),
    ToolInfo("passerine", "passerine", "turns MIDI files into CHRP files for Phoenix Orchestra", "standalone"),
)

_BY_NAME = {t.name.lower(): t for t in CATALOG}


def platform_note(tool: str, platform: str) -> str | None:
    """CTRE's stated reason a tool has no build for this platform, when there is one."""
    return info(tool).not_on.get(platform)


def info(tool: str) -> ToolInfo:
    """The catalog entry for a tool. A tool the index has but the catalog lacks gets a generic entry, so a new CTRE tool still lists and installs."""
    return _BY_NAME.get(tool.lower()) or ToolInfo(tool, tool.lower(), "CTRE tool", "standalone")

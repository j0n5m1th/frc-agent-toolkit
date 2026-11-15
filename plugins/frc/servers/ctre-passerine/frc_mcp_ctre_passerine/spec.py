"""The Passerine server's `SPEC`: what it is, its `check` and its command line (`mcp-ctre-passerine`).
`config.py` (where files may be), `passerine.py` (running it), `tools/`, `app.py`. Version choice and folder rules are `frc_mcp_ctre`.
"""

import argparse

from frc_mcp_core.spec import ServerSpec

from . import config


def _check(_args: argparse.Namespace) -> int:
    from frc_mcp_ctre.errors import (
        CtreError,  # imported here so that importing the spec stays cheap
    )
    from frc_mcp_ctre.resolve import newest_installed

    print("\n".join(config.MIDI.describe("MIDI files")))
    try:
        found = newest_installed("passerine")
    except CtreError as e:
        print(f"  {e}")
        return 1
    print(f"  passerine: {found.version} ({found.source})")
    return 0


SPEC = ServerSpec(
    key="ctre-passerine",
    name="ctre_passerine_mcp",
    description="Passerine (MIDI to CHRP) MCP server; check is a read-only look at what it would use.",
    instructions="MIDI to CHRP (Phoenix Orchestra music): list and convert; the .chrp lands beside the MIDI. Robot code loads it with Orchestra.loadMusic. Missing: ctre_install.",
    tool_modules=("frc_mcp_ctre_passerine.tools.passerine_tools",),
    check=_check,
)


def main() -> None:
    SPEC.main()

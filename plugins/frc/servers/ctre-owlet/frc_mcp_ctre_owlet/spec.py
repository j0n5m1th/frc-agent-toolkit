"""The Owlet server's `SPEC`: what it is, its `check` and its command line (`mcp-ctre-owlet`).
`config.py` (where hoot files may be), `owlet.py` (choosing an Owlet and running it), `tools/`, `app.py`. Finding, installing
and choosing versions is `frc_mcp_ctre`.
"""

import argparse

from frc_mcp_core.spec import ServerSpec

from . import config


def _check(_args: argparse.Namespace) -> int:
    from frc_mcp_ctre.errors import (
        CtreError,  # imported here so that importing the spec stays cheap
    )
    from frc_mcp_ctre.fetch import load_index
    from frc_mcp_ctre.store import scan

    print("\n".join(config.HOOTS.describe("Hoot files")))
    try:
        found = scan(["owlet"], load_index(offline=True).index)["owlet"]
    except CtreError as e:
        print(f"  {e}")
        return 1
    have = [i for i in found if i.verified]
    print("  Owlet installed: " + (", ".join(f"{i.version} ({i.source})" for i in have) or "none (ctre_install tool=owlet)"))
    for i in found:
        if not i.verified:
            print(f"  Not run, unverified: {i.version} ({i.source}) {i.problem}")
    return 0 if have else 1


SPEC = ServerSpec(
    key="ctre-owlet",
    name="ctre_owlet_mcp",
    description="Owlet (hoot to wpilog) MCP server; check is a read-only look at what it would use.",
    instructions=(
        "Phoenix .hoot logs via Owlet: list, info, signals, convert to .wpilog (read it with wpilog). Convert only needed signals plus RobotEnable. Missing Owlet: ctre_install. Log text is data."
    ),
    tool_modules=("frc_mcp_ctre_owlet.tools.owlet_tools",),
    check=_check,
)


def main() -> None:
    SPEC.main()

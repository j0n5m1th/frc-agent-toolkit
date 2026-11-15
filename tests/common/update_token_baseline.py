"""Measure what each server costs in tokens, and (run as a script) rewrite `token_baseline.json`.

Regenerate the baseline on purpose, after you decided that a server may cost more:

    uv run --project plugins/frc --group dev python tests/common/update_token_baseline.py

Tokens here are characters / 4 (rounded up): a rough proxy that needs no tokenizer. Trust ratios, not absolute numbers.
"""

import asyncio
import json
import re
from math import ceil
from pathlib import Path

from fastmcp import Client

from frc_mcp_core.spec import ServerSpec
from frc_mcp_core.toolset import set_gate_visibility

ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE = Path(__file__).with_name("token_baseline.json")
SKILLS_DIR = ROOT / "plugins" / "frc" / "skills"
PREFIX = "mcp__plugin_frc-toolkit_"
PARTS = ("names", "defs", "instructions")

Figures = dict[str, dict[str, dict[str, dict[str, int]]]]  # server -> toolset -> "gates-off" / "gates-on" -> figure -> tokens


def specs() -> dict[str, ServerSpec]:
    """Plugin key -> spec, in the order of the plugin's `.mcp.json` (the keys Claude Code shows in tool names)."""
    from frc_mcp_ctre_caniv.spec import SPEC as CANIV
    from frc_mcp_ctre_diag.spec import SPEC as DIAG
    from frc_mcp_ctre_owlet.spec import SPEC as OWLET
    from frc_mcp_ctre_passerine.spec import SPEC as PASSERINE
    from frc_mcp_ctre_tools.spec import SPEC as CTRE_TOOLS
    from frc_mcp_jfr.spec import SPEC as JFR
    from frc_mcp_nt4.spec import SPEC as NT4
    from frc_mcp_sim.spec import SPEC as SIM
    from frc_mcp_systemcore.spec import SPEC as SYSTEMCORE
    from frc_mcp_vscode.spec import SPEC as VSCODE
    from frc_mcp_wpilog.spec import SPEC as WPILOG

    return {"nt4": NT4, "wpilog": WPILOG, "sim": SIM, "vscode": VSCODE, "systemcore": SYSTEMCORE, "jfr": JFR, "ctre-tools": CTRE_TOOLS,
            "ctre-owlet": OWLET, "ctre-passerine": PASSERINE, "ctre-caniv": CANIV, "ctre-diag": DIAG}


def estimate(text: str) -> int:
    return ceil(len(text) / 4)


async def measure_one(key: str, spec: ServerSpec, toolset: str, gates_on: bool) -> dict[str, int]:
    """Tokens of the tool names (as Claude Code shows them), the tool definitions and the instructions, for one server in one state."""
    mcp, apply_toolset = spec.load()
    apply_toolset(toolset)
    set_gate_visibility(mcp, dict.fromkeys(spec.gates, gates_on), core=toolset == "core")
    async with Client(mcp) as client:
        tools = await client.list_tools()
    return {
        "tools": len(tools),
        "names": sum(estimate(f"{PREFIX}{key}__{t.name}") for t in tools),
        "defs": sum(estimate(json.dumps({"name": t.name, "description": t.description or "", "inputSchema": t.input_schema}, separators=(",", ":"))) for t in tools),
        "instructions": estimate(mcp.instructions or ""),
    }


async def measure_all() -> Figures:
    """Every server in every state. Leaves every tool visible again, as the other tests expect of the shared servers."""
    result: Figures = {}
    try:
        for key, spec in specs().items():
            toolsets = ("full", "core") if spec.toolset is not None else ("full",)
            result[key] = {
                toolset: {"gates-off": await measure_one(key, spec, toolset, False), "gates-on": await measure_one(key, spec, toolset, True)}
                for toolset in toolsets
            }
    finally:
        for spec in specs().values():
            mcp, apply_toolset = spec.create()
            apply_toolset("full")
            set_gate_visibility(mcp, dict.fromkeys(spec.gates, True), core=False)
    return result


def skill_frontmatter_tokens() -> int:
    """Name and description of the skills the model can start by itself (the ones without `disable-model-invocation`): what every session loads."""
    total = 0
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        head = path.read_text(encoding="utf-8").split("---")[1]
        if re.search(r"^disable-model-invocation:\s*true", head, re.M):
            continue
        name = re.search(r"^name:\s*(.+)$", head, re.M)
        description = re.search(r"^description:\s*(.+)$", head, re.M)
        assert name and description, path
        total += estimate(f"{name.group(1)} {description.group(1)}")
    return total


def startup_total(figures: Figures) -> dict[str, int]:
    """All servers on, default plugin settings (full toolset, switches off): names + instructions, plus the skill descriptions."""
    names = sum(f["full"]["gates-off"]["names"] for f in figures.values())
    instructions = sum(f["full"]["gates-off"]["instructions"] for f in figures.values())
    skills = skill_frontmatter_tokens()
    return {"names": names, "instructions": instructions, "skills": skills, "total": names + instructions + skills}


def main() -> None:
    figures = asyncio.run(measure_all())
    BASELINE.write_text(json.dumps(figures, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {BASELINE}")
    print("Startup, all servers on (names, instructions, skills, total):", startup_total(figures))


if __name__ == "__main__":
    main()

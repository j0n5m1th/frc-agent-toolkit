"""The entry docs: links work, SETTINGS.md lists every setting, and the README stays short."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "frc"
DOCS = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, drop everything but letters, digits, spaces, hyphens and underscores, spaces to hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "").strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    found: dict[str, int] = {}
    result: set[str] = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and (match := re.match(r"#{1,6}\s+(.*?)\s*#*$", line)):
            base = slug(match.group(1))
            count = found.get(base, 0)
            found[base] = count + 1
            result.add(base if count == 0 else f"{base}-{count}")
    return result


def links(path: Path) -> list[str]:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)  # a link in a code block is an example
    text = re.sub(r"`[^`\n]*`", "", text)
    return LINK.findall(text)


def test_every_relative_link_points_at_a_file_and_heading_that_exist():
    problems = []
    for path in DOCS:
        for target in links(path):
            if re.match(r"[a-z][a-z0-9+.-]*:", target):  # https:, mailto:
                continue
            name, _, anchor = target.partition("#")
            destination = (path.parent / name).resolve() if name else path
            if not destination.exists():
                problems.append(f"{path.relative_to(ROOT)}: {target} (no such file)")
            elif anchor and destination.suffix == ".md" and anchor not in anchors(destination):
                problems.append(f"{path.relative_to(ROOT)}: {target} (no such heading)")
    assert not problems, "\n".join(problems)


def test_settings_doc_names_every_switch_toolset_and_dialog_field():
    text = (ROOT / "docs" / "SETTINGS.md").read_text(encoding="utf-8")
    names: set[str] = set()
    for config in PLUGIN.glob("*/*/frc_mcp_*/config.py"):
        names.update(re.findall(r"\b[A-Z][A-Z0-9]*_(?:ALLOW_[A-Z0-9_]+|TOOLSET)\b", config.read_text(encoding="utf-8")))
    assert {"NT4_TOOLSET", "VSCODE_ALLOW_DEPLOY", "CANIV_ALLOW_FLASH", "DIAG_ALLOW_ANY_HOST"} <= names  # the search found the configs
    fields = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["userConfig"]
    missing = sorted(name for name in (*names, *fields) if name not in text)
    assert not missing, f"docs/SETTINGS.md does not mention: {missing}"


def test_settings_doc_titles_match_the_plugin_dialog():
    text = (ROOT / "docs" / "SETTINGS.md").read_text(encoding="utf-8")
    for name, field in json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["userConfig"].items():
        assert field["title"] in text, name


def test_readme_is_short_and_has_the_install_lines():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert 350 <= len(text.split()) <= 650
    assert "/plugin install frc-toolkit@frc-toolkit" in text and "/plugin marketplace add j0n5m1th/frc-agent-toolkit" in text


def tool_names(key: str) -> set[str]:
    """Every tool of a server with every switch on and the full toolset (the same view the plugin hook test uses)."""
    import asyncio

    from fastmcp import Client

    from frc_mcp_core import command
    from frc_mcp_core.toolset import set_gate_visibility

    async def load() -> set[str]:
        spec = command.load_spec(key)
        mcp, _ = spec.load()
        set_gate_visibility(mcp, dict.fromkeys(spec.gates, True), core=False)
        async with Client(mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    return asyncio.run(load())


def test_the_nt4_and_wpilog_guides_name_every_tool_of_their_server():
    for key, guide in (("nt4", ("NT4.md",)), ("wpilog", ("WPILOG.md", "WPILOG_ADVANCED.md"))):
        tools = tool_names(key)
        assert len(tools) > 8, key  # the search found the server
        missing = sorted(name for name in tools if f"`{name}`" not in doc_text(*guide))
        assert not missing, f"docs/{guide[0]} does not mention: {missing}"


def test_the_server_guides_keep_to_their_length():
    for guide, low, high in (("NT4.md", 1100, 2000), ("WPILOG.md", 1100, 1900), ("WPILOG_ADVANCED.md", 800, 1500)):
        words = len((ROOT / "docs" / guide).read_text(encoding="utf-8").split())
        assert low <= words <= high, f"docs/{guide} has {words} words (allowed {low} to {high})"


def test_the_docs_that_were_merged_are_gone():
    for name in ("REFERENCE.md", "NT4_EXAMPLES.md", "WPILOG_EXAMPLES.md", "CTRE_DIAG.md", "ROADMAP.md", "LEARNING.md"):
        assert not (ROOT / "docs" / name).exists(), name
    assert not (ROOT / "evaluations" / "BENCHMARKS.md").exists()


def doc_text(*names: str) -> str:
    """The text of one guide that is split over several files."""
    return "\n".join((ROOT / "docs" / name).read_text(encoding="utf-8") for name in names)


def test_the_ctre_guide_names_every_tool_of_the_five_ctre_servers():
    text = doc_text("CTRE.md", "CTRE_DEVICES.md")
    for key in ("ctre-tools", "ctre-owlet", "ctre-passerine", "ctre-caniv", "ctre-diag"):
        tools = tool_names(key)
        assert tools, key
        missing = sorted(name for name in tools if f"`{name}`" not in text)
        assert not missing, f"docs/CTRE.md does not mention: {missing}"


def test_the_systemcore_guide_names_every_tool_and_no_removed_one():
    text = (ROOT / "docs" / "SYSTEMCORE.md").read_text(encoding="utf-8")
    tools = tool_names("systemcore")
    assert len(tools) > 8
    missing = sorted(name for name in tools if f"`{name}`" not in text)
    assert not missing, f"docs/SYSTEMCORE.md does not mention: {missing}"
    assert "systemcore_ssh_trust" not in text


def test_the_sim_vscode_and_jfr_guides_name_every_tool_of_their_server():
    for key, guide in (("sim", "SIM.md"), ("vscode", "VSCODE.md"), ("jfr", "JFR.md")):
        text = (ROOT / "docs" / guide).read_text(encoding="utf-8")
        tools = tool_names(key)
        assert tools, key
        missing = sorted(name for name in tools if f"`{name}`" not in text)
        assert not missing, f"docs/{guide} does not mention: {missing}"


def test_the_other_docs_keep_to_their_length():
    # Targets from the docs plan; the band is -25% and +30%. The big guides were split (ARCHITECTURE, SETTINGS, WPILOG, CTRE) so that no
    # file is overwhelming to read; each part has its own target.
    for guide, target in (("CTRE.md", 1450), ("CTRE_DEVICES.md", 870), ("SYSTEMCORE.md", 1250), ("SIM.md", 500), ("VSCODE.md", 500), ("JFR.md", 550), ("GETTING_STARTED.md", 1000),
                          ("FAQ.md", 550), ("ARCHITECTURE.md", 1750), ("ARCHITECTURE_SERVERS.md", 1150), ("ARCHITECTURE_NOTES.md", 930), ("SETTINGS.md", 1700), ("SAFETY.md", 1150)):
        words = len((ROOT / "docs" / guide).read_text(encoding="utf-8").split())
        low, high = int(target * 0.75), int(target * 1.3)
        assert low <= words <= high, f"docs/{guide} has {words} words (allowed {low} to {high})"


def test_no_guide_is_overwhelming():
    """A guide longer than this is split into parts, each with a pointer to the others."""
    too_long = {path.name: len(path.read_text(encoding="utf-8").split()) for path in (ROOT / "docs").glob("*.md")}
    assert not {name: words for name, words in too_long.items() if words > 2000}


def test_contributing_and_agents_stay_short():
    for name, high in (("CONTRIBUTING.md", 1500), ("AGENTS.md", 1000)):
        words = len((ROOT / name).read_text(encoding="utf-8").split())
        assert words <= high, f"{name} has {words} words (allowed {high})"


def student_docs() -> list[Path]:
    return [ROOT / "README.md", ROOT / "plugins" / "frc" / "README.md", *(ROOT / "docs").glob("*.md")]


def test_every_uvx_command_pins_python_313():
    for path in student_docs():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "uvx " in line and "git+" in line:
                assert "--python 3.13" in line, f"{path.name}: {line.strip()}"


def test_docs_do_not_say_servers_start_off_or_name_an_undocumented_command():
    for path in student_docs():
        text = path.read_text(encoding="utf-8")
        assert "/plugin configure" not in text, path.name  # not in Claude Code's docs: `/config` is
        assert not re.search(r"switch(?:ed)? on `?\w[\w-]*`? in `/mcp`", text), f"{path.name} says to switch a server on; plugin servers start on"


def test_docs_say_plugin_folders_are_fixed_and_nt4_writes_need_your_own_entry():
    settings = doc_text("SETTINGS.md", "SAFETY.md")
    assert "copy it into your project folder" in settings
    assert "Writes cannot be turned on from the plugin dialog" in settings and "onFailure" in settings


def test_docs_state_the_minimum_claude_code_version_and_what_the_hook_does_without_it():
    for name in ("README.md", "docs/GETTING_STARTED.md", "docs/SAFETY.md", "plugins/frc/README.md"):
        assert "2.1.295" in (ROOT / name).read_text(encoding="utf-8"), name
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert '"onFailure": "block"' in json.dumps(hooks, indent=2)  # the reason for the minimum version
    settings = doc_text("SETTINGS.md", "SAFETY.md")
    assert "a failed hook lets calls through" in settings and "not tested in a real session" not in settings


def test_docs_do_not_overclaim_or_contradict_the_code():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "The servers only read" not in readme and "Gradle" in readme
    settings = doc_text("SETTINGS.md", "SAFETY.md")
    assert "never follows a redirect from a device" in settings and "do follow redirects" in settings
    assert "`NT4_ALLOW_WRITES` and `NT4_WRITE_PREFIXES` are not in it" in settings
    env = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["nt4"]["env"]
    assert "NT4_ALLOW_WRITES" not in env and "NT4_WRITE_PREFIXES" not in env  # the doc sentence above says so
    assert "In the plugin both are your project folder" not in (ROOT / "docs" / "NT4.md").read_text(encoding="utf-8")


def test_docs_explain_the_systemcore_ipv6_rule_and_the_env_quote_rule():
    systemcore = (ROOT / "docs" / "SYSTEMCORE.md").read_text(encoding="utf-8")
    assert "`::1`" in systemcore and "fe80::" in systemcore and "fc00::/7" in systemcore
    assert "must end with one" in (ROOT / "docs" / "SETTINGS.md").read_text(encoding="utf-8")


def test_the_start_here_skill_and_readme_table_name_the_servers_each_skill_needs():
    start = (PLUGIN / "skills" / "frc-start-here" / "SKILL.md").read_text(encoding="utf-8")
    assert "wpilib-build" in start and start.count("`ctre-tools`") >= 2
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "`wpilib-build`" in readme and readme.count("`ctre-tools`") >= 3

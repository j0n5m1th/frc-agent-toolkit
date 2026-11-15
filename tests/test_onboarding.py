"""Verify the portable example configuration (docs/mcp.json.example) and the plugin's settings."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ("nt4", "wpilog", "sim", "vscode", "systemcore", "jfr", "ctre-tools", "ctre-owlet", "ctre-passerine", "ctre-caniv", "ctre-diag")


def example_servers() -> dict:
    return json.loads((ROOT / "docs" / "mcp.json.example").read_text(encoding="utf-8"))["mcpServers"]


def test_the_old_example_files_are_gone():
    assert not (ROOT / ".mcp.json.example").exists() and not (ROOT / ".mcp.json.uvx.example").exists()
    assert not (ROOT / ".mcp.json").exists()  # the repo has no project MCP config: only the plugin has one


def test_example_config_has_every_server_and_runs_frc_mcp_from_a_clone():
    servers = example_servers()
    assert sorted(servers) == sorted(KEYS)
    for key, server in servers.items():
        assert server["command"] == "uv"
        assert server["args"] == ["run", "--project", "plugins/frc", "--frozen", "--no-dev", "--python", "3.13", "frc-mcp", key]  # no --directory, no home-folder paths


def test_example_config_matches_the_demo_defaults_and_is_read_only():
    servers = example_servers()
    assert servers["nt4"]["env"] == {"NT4_HOST": "127.0.0.1", "NT4_PORT": "5810", "NT4_READ_ONLY": "1"}  # `frc-mcp demo robot` listens on 5810
    assert servers["ctre-diag"]["env"] == {"DIAG_HOST": "172.30.0.1"}  # the Systemcore's own Wi-Fi address
    for key, server in servers.items():
        assert not any("ALLOW" in name for name in server["env"]), key  # writing, deploying or installing is never on in an example


def test_example_config_asks_for_explicit_folders():
    servers = example_servers()
    for key, setting in (("wpilog", "WPILOG_DIR"), ("sim", "SIM_PROJECT_DIR"), ("vscode", "VSCODE_PROJECT_DIR"), ("jfr", "JFR_DIR"),
                         ("systemcore", "SYSTEMCORE_DOWNLOAD_DIR"), ("ctre-tools", "CTRE_PROJECT_DIR"), ("ctre-owlet", "OWLET_DIR"),
                         ("ctre-passerine", "PASSERINE_DIR"), ("ctre-caniv", "CANIV_FIRMWARE_DIR")):
        assert list(servers[key]["env"]) == [setting] and servers[key]["env"][setting].startswith("REPLACE_WITH"), key


def test_the_plugin_bundles_the_ctre_servers_and_their_skills_and_every_permission_is_off_by_default():
    plugin = Path(__file__).resolve().parent.parent / "plugins" / "frc"
    servers = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    for key in ("ctre-tools", "ctre-owlet", "ctre-passerine", "ctre-caniv", "ctre-diag"):
        assert servers[key]["args"][-2:] == ["frc-mcp", key]
    user_config = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["userConfig"]
    assert [user_config[name]["default"] for name in ("allow_ctre_install", "allow_caniv_config", "allow_caniv_flash")] == [False, False, False]
    for server in ("ctre-tools", "ctre-owlet", "ctre-passerine", "ctre-caniv", "ctre-diag"):
        for value in servers[server]["env"].values():
            assert value == "${CLAUDE_PROJECT_DIR}" or value.removeprefix("${user_config.").removesuffix("}") in user_config  # every placeholder names a real setting
    for skill in ("ctre-diagnose", "ctre-convert", "ctre-canivore", "wpilib-project", "frc-start-here", "wpilib-log-analysis"):
        text = (plugin / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith(f"---\nname: {skill}\n") and len(text.split()) < 250


def test_the_plugin_license_matches_the_project_license():
    import tomllib
    root = Path(__file__).resolve().parent.parent
    plugin = json.loads((root / "plugins" / "frc" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "plugins" / "frc" / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert plugin["license"] == project["license"]["text"] == "AGPL-3.0-only"
    for folder in (root, root / "plugins" / "frc"):
        assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (folder / "LICENSE").read_text(encoding="utf-8")


def test_the_code_review_checklist_ships_with_its_skill_and_cites_every_source():
    skill = Path(__file__).resolve().parent.parent / "plugins" / "frc" / "skills" / "wpilib-project"
    assert "references/checklist.md" in (skill / "SKILL.md").read_text(encoding="utf-8")
    checklist = (skill / "references" / "checklist.md").read_text(encoding="utf-8")
    rules = [line for line in checklist.splitlines() if line.startswith("- **")]
    assert len(rules) > 40
    for heading in ("## 1. WPILib", "## 3. CTRE Phoenix 6", "## 4. AdvantageKit", "## 5. 2027"):
        assert heading in checklist


def test_the_docs_explain_the_settings_that_are_not_in_the_plugin_dialog():
    text = (ROOT / "docs" / "SETTINGS.md").read_text(encoding="utf-8")
    assert "### How to set the ones that are not in the dialog" in text
    for word in ("CLAUDE_PROJECT_DIR", "CTRE_TOOLS_DIR", "SYSTEMCORE_DOWNLOAD_DIR", "CANIV_FIRMWARE_DIR", "5810", "every project"):
        assert word in text, word


def test_skills_tell_students_the_plugin_setting_not_an_environment_variable():
    for path in (ROOT / "plugins" / "frc" / "skills").glob("*/SKILL.md"):
        assert not re.search(r"(?:JFR|CTRE|CANIV|SYSTEMCORE|VSCODE)_ALLOW_\w+", path.read_text(encoding="utf-8")), path.parent.name

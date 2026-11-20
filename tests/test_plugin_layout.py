"""The plugin's shape (plugins/frc/): one `.mcp.json` entry per server folder, the same command for each, nothing extra inside."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "frc"
FLAGS = ["--project", "${CLAUDE_PLUGIN_ROOT}", "--frozen", "--no-dev", "--python", "3.13"]
SKILLS = sorted(path.parent for path in (PLUGIN / "skills").glob("*/SKILL.md"))


def server_keys() -> list[str]:
    return sorted(path.name for path in (PLUGIN / "servers").iterdir() if path.is_dir() and not path.name.startswith("."))


def mcp_servers() -> dict:
    return json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]


def test_mcp_json_has_exactly_one_entry_per_server_folder():
    assert len(server_keys()) == 11
    assert sorted(mcp_servers()) == server_keys()


def test_every_entry_runs_frc_mcp_from_the_plugin_folder_with_all_five_flags():
    for key, server in mcp_servers().items():
        assert server["command"] == "uv", key
        args = server["args"]
        assert args[0] == "run" and args[-2:] == ["frc-mcp", key], key
        assert args[1:-2] == FLAGS, key  # --project, --frozen (no re-lock), --no-dev (no test tools), --python 3.13, in this order
        assert all(isinstance(value, str) for value in server["env"].values()), key


DIALOG_FIELDS = {"nt4_host", "systemcore_host", "diag_host", "nt4_read_only", "allow_record", "allow_deploy", "allow_prune", "allow_provision",
                 "allow_wifi_join", "allow_restart", "allow_ctre_install", "allow_caniv_config", "allow_caniv_flash", "read_outside_logs", "learning_mode"}
PROJECT = "${CLAUDE_PROJECT_DIR}"
FOLDER_SETTINGS = {"WPILOG_DIR", "SIM_PROJECT_DIR", "VSCODE_PROJECT_DIR", "JFR_DIR", "CTRE_PROJECT_DIR", "OWLET_DIR", "PASSERINE_DIR"}
UNSET_ON_PURPOSE = {"CTRE_TOOLS_DIR", "SYSTEMCORE_DOWNLOAD_DIR", "CANIV_FIRMWARE_DIR", "NT4_PORT"}  # the server's own default is better than the project folder


def user_config() -> dict:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["userConfig"]


def test_every_entry_matches_its_entry_point_and_setting_placeholders_are_real_settings():
    fields = user_config()
    for key, server in mcp_servers().items():
        assert f'{key} = "frc_mcp_' in (PLUGIN / "servers" / key / "pyproject.toml").read_text(encoding="utf-8"), key
        for name, value in server["env"].items():
            assert value == PROJECT or (re.fullmatch(r"\$\{user_config\.(\w+)\}", value) and value[len("${user_config."):-1] in fields), (key, name, value)


def test_the_dialog_has_exactly_the_fifteen_fields_and_every_field_is_used():
    fields = user_config()
    assert set(fields) == DIALOG_FIELDS and len(fields) == 15
    used = {value[len("${user_config."):-1] for server in mcp_servers().values() for value in server["env"].values() if value != PROJECT}
    assert used == DIALOG_FIELDS


def test_every_allow_switch_is_a_boolean_that_starts_off_and_is_titled_allow():
    fields = user_config()
    for name in sorted(DIALOG_FIELDS):
        field = fields[name]
        assert field["type"] == ("string" if name.endswith("_host") else "boolean"), name
        assert isinstance(field["default"], str if name.endswith("_host") else bool), name
        assert set(field) == {"type", "title", "description", "default"}, name
        if name.startswith("allow_"):
            assert field["default"] is False and field["title"].startswith("Allow "), name
            assert "every project" in field["description"], name  # these values are saved per user, not per project
    assert fields["nt4_read_only"]["default"] is True and fields["nt4_read_only"]["type"] == "boolean"
    assert fields["read_outside_logs"]["default"] is True  # on, because Claude Code asks before every such read (hooks.json); an allow_ switch would start off
    assert fields["nt4_host"]["default"] == "127.0.0.1" and fields["systemcore_host"]["default"] == "" and fields["diag_host"]["default"] == ""


def test_folders_follow_the_project_and_the_leftover_settings_are_not_forced():
    seen: dict[str, str] = {}
    for server in mcp_servers().values():
        seen.update(server["env"])
    assert {name for name, value in seen.items() if value == PROJECT} == FOLDER_SETTINGS
    assert not UNSET_ON_PURPOSE & set(seen)
    for name in seen:
        assert not name.endswith("_DIR") or seen[name] == PROJECT, name  # no folder is read from the dialog


def test_each_allow_switch_reaches_exactly_one_server_setting():
    seen = {value: (key, name) for key, server in mcp_servers().items() for name, value in server["env"].items() if "allow_" in value}
    assert sorted(seen) == sorted(f"${{user_config.{name}}}" for name in DIALOG_FIELDS if name.startswith("allow_"))
    for key, name in seen.values():
        assert "ALLOW" in name, (key, name)


def test_the_manifest_is_valid_and_named_frc():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "frc-toolkit"


def test_the_plugin_has_no_commands_bin_scripts_or_repo_only_folders():
    for name in ("commands", "bin", "tests", "docs", "evaluations", "functional_tests"):
        assert not (PLUGIN / name).exists(), name
    for skill in SKILLS:
        assert [path.name for path in skill.rglob("*") if path.suffix in {".sh", ".ps1", ".bat", ".cmd", ".py"}] == [], skill.name
    assert not (PLUGIN / "CLAUDE.md").exists() and not (PLUGIN / ".claude").exists()


def test_no_empty_component_folders():
    for name in ("skills", "hooks", "agents", "servers", "lib"):  # hooks/ holds only hooks.json (see test_plugin_hooks.py)
        folder = PLUGIN / name
        assert not folder.exists() or any(folder.iterdir()), name


def test_the_repo_does_not_load_the_plugin_servers_as_project_servers():
    assert not (ROOT / ".mcp.json").exists()
    assert (ROOT / "CLAUDE.md").is_file() and not (PLUGIN / "CLAUDE.md").exists()
    settings = ROOT / ".claude" / "settings.json"
    assert not settings.exists() or "mcpServers" not in settings.read_text(encoding="utf-8")


SKILL_NAMES = {"frc-explain", "frc-reasoning", "wpilib-prematch", "wpilib-triage", "wpilib-tune", "frc-start-here", "systemcore", "systemcore-live", "systemcore-restart", "systemcore-provision", "ctre-diagnose", "ctre-convert", "ctre-canivore",
               "wpilib-build", "wpilib-deploy", "wpilib-project", "wpilib-live", "wpilib-log-analysis", "wpilib-profile"}
GATED = {"systemcore-restart", "systemcore-provision", "ctre-canivore", "wpilib-deploy"}  # side effects: the model may not start these alone


def frontmatter(skill: Path) -> dict[str, str]:
    block = (skill / "SKILL.md").read_text(encoding="utf-8").split("---" + chr(10))[1]
    return dict(line.split(": ", 1) for line in block.splitlines())


def test_the_plugin_has_exactly_these_nineteen_skills():
    assert {skill.name for skill in SKILLS} == SKILL_NAMES and len(SKILLS) == 19


def test_exactly_the_four_side_effect_skills_are_hidden_from_the_model():
    flagged = {skill.name for skill in SKILLS if frontmatter(skill).get("disable-model-invocation") == "true"}
    assert flagged == GATED
    for skill in SKILLS:
        assert set(frontmatter(skill)) <= {"name", "description", "disable-model-invocation"}, skill.name  # no version, no display name


def test_descriptions_of_skills_the_model_sees_stay_short_and_say_when_to_use_them():
    for skill in SKILLS:
        description = frontmatter(skill)["description"]
        assert "Use " in description, skill.name
        if skill.name not in GATED:
            assert len(description.split()) <= 34, (skill.name, len(description.split()))  # about 45 tokens


def test_every_skill_name_is_its_folder_name():
    for skill in SKILLS:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith(f"---\nname: {skill.name}\n"), skill.name


def test_skills_name_only_real_server_keys():
    keys = set(server_keys())
    for skill in SKILLS:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        for prefix in re.findall(r"mcp__(?:plugin_frc-toolkit_)?([\w-]+?)__", text):
            assert prefix in keys, (skill.name, prefix)
        assert not re.search(r"wpilib-(nt4|wpilog|vscode)\b|wpilib-sim(?!-check)|plugin_frc-toolkit_wpilib", text), skill.name
        if skill.name in ("frc-explain", "frc-reasoning"):  # they work with what the conversation already holds, so they need no server
            assert "Needs no server" in text
            continue
        if skill.name == "frc-start-here":  # it surveys every server, so it says which to switch on instead of stopping
            assert "`/mcp`" in text and {"vscode", "wpilog", "sim", "nt4", "ctre-tools"} <= set(re.findall(r"`([\w-]+)`", text))
            continue
        needs = re.search(r"^Needs ([^;\n]+); if tools are missing, say which to switch back on in `/mcp`, then stop\.$", text, re.MULTILINE)
        assert needs, f"{skill.name} must say which server it needs, to switch it back on in /mcp, and to stop"
        named = re.findall(r"`([\w-]+)`", needs.group(1))
        assert named and set(named) <= keys, (skill.name, named)


def test_every_skill_stays_under_250_words():
    for skill in SKILLS:
        assert len((skill / "SKILL.md").read_text(encoding="utf-8").split()) < 250, skill.name


def test_plugin_readme_is_short_and_license_matches_the_root():
    words = len((PLUGIN / "README.md").read_text(encoding="utf-8").split())
    assert 100 <= words <= 250, words
    assert (PLUGIN / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()


def test_every_server_gets_the_learning_mode_setting():
    for key, server in mcp_servers().items():
        assert server["env"].get("FRC_LEARNING_MODE") == "${user_config.learning_mode}", key

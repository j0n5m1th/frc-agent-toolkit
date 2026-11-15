import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("changelog_section", ROOT / ".github" / "scripts" / "changelog_section.py")
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

SAMPLE = """# Changelog

## [Unreleased]

- next thing

## [1.2.0] - 2030-01-01

- one
- two

## [1.1.0]

- old
"""


def test_extracts_middle_section():
    assert mod.changelog_section(SAMPLE, "1.2.0") == "- one\n- two\n"


def test_extracts_last_section():
    assert mod.changelog_section(SAMPLE, "1.1.0") == "- old\n"


def test_missing_version_raises():
    with pytest.raises(ValueError):
        mod.changelog_section(SAMPLE, "9.9.9")


def test_dot_is_not_a_wildcard():
    with pytest.raises(ValueError):
        mod.changelog_section(SAMPLE, "1x2x0")


def test_real_changelog_has_current_version():
    version = json.loads((ROOT / "plugins/frc/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert mod.changelog_section(text, version).strip()

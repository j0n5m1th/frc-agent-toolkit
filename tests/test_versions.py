"""One version everywhere: plugin.json, every pyproject, the marketplace entry (its pinned tag too) and any concrete uvx tag in the docs."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "frc"


def plugin_version() -> str:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def pyproject_files() -> list[Path]:
    return [PLUGIN / "pyproject.toml", *sorted(PLUGIN.glob("servers/*/pyproject.toml")), *sorted(PLUGIN.glob("lib/*/pyproject.toml"))]


def test_every_pyproject_has_the_plugin_version():
    files = pyproject_files()
    assert len(files) >= 16
    for path in files:
        version = tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"]
        assert version == plugin_version(), path.relative_to(ROOT).as_posix()


def test_marketplace_entry_is_pinned_to_the_release_tag():
    # Students get exactly the tagged code, not whatever is on `master`. A release commit sets this together with the version.
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    entries = [entry for entry in marketplace["plugins"] if entry["name"] == "frc-toolkit"]
    assert len(entries) == 1
    source = entries[0]["source"]
    assert isinstance(source, dict), "use a git source with a ref, not a relative path"
    assert source["source"] == "git-subdir" and source["path"] == "plugins/frc"
    assert source["url"] == "https://github.com/j0n5m1th/frc-agent-toolkit.git"
    assert source["ref"] == f"v{plugin_version()}"
    if "version" in entries[0]:
        assert entries[0]["version"] == plugin_version()


def test_concrete_uvx_tags_in_docs_match_the_version():
    pattern = re.compile(r"git\+https://github\.com/[^\s\"'@]+@([^\s\"'#]+)#subdirectory=plugins/frc")
    docs = [*ROOT.glob("*.md"), *(ROOT / "docs").glob("*.md"), *PLUGIN.glob("**/*.md")]
    for path in docs:
        for tag in pattern.findall(path.read_text(encoding="utf-8")):
            if tag.startswith("<") and tag.endswith(">"):
                continue  # placeholder until the first release
            assert tag in (plugin_version(), f"v{plugin_version()}"), (path.name, tag)


def test_changelog_has_an_unreleased_section_and_the_current_version():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in text
    assert f"## [{plugin_version()}]" in text

"""Checks on the CI files. No network."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GH = ROOT / ".github"
WORKFLOWS = sorted((GH / "workflows").glob("*.yml"))
SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
# Actions allowed without a SHA pin, with the reason. Empty when every pin was verified.
UNPINNED_ALLOWLIST: dict[str, str] = {}


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def uses(path: Path) -> list[str]:
    return re.findall(r"^\s*-?\s*uses:\s*(\S+)", path.read_text(encoding="utf-8"), re.M)


def test_workflows_exist():
    names = {p.name for p in WORKFLOWS}
    assert {"test.yml", "release.yml"} <= names


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_rules(path):
    text = path.read_text(encoding="utf-8")
    data = load(path)
    assert "permissions" in data, "set top-level permissions"
    assert "pull_request_target" not in text
    for ref in uses(path):
        assert SHA.match(ref) or ref in UNPINNED_ALLOWLIST, f"pin {ref} by commit SHA"
    for line in text.splitlines():
        if re.search(r"\buv (run|sync)\b", line):
            assert "--project plugins/frc" in line, line
            assert "--python 3.13" in line, line  # the interpreter the plugin runs on, not whatever uv finds first


def test_matrix_has_three_oses_and_runs_on_pull_requests():
    data = load(GH / "workflows" / "test.yml")
    assert set(data["jobs"]["test"]["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest", "macos-latest"}
    triggers = data[True] if True in data else data["on"]  # YAML 1.1 reads a bare `on` as True
    assert "pull_request" in triggers


def test_release_has_no_pypi():
    text = (GH / "workflows" / "release.yml").read_text(encoding="utf-8").lower()
    for word in ("pypi", "twine", "uv publish"):
        assert word not in text


def test_dependabot():
    data = load(GH / "dependabot.yml")
    kinds = {u["package-ecosystem"]: u["directory"] for u in data["updates"]}
    assert kinds == {"uv": "/plugins/frc", "github-actions": "/"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_uv_and_claude_code_versions_are_pinned(path):
    steps = [step for job in load(path)["jobs"].values() for step in job["steps"]]
    for step in steps:
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
            assert re.fullmatch(r"\d+\.\d+\.\d+", str(step.get("with", {}).get("version", ""))), "pin the uv version"
        if "npm install" in step.get("run", ""):
            assert re.search(r"@anthropic-ai/claude-code@\d+\.\d+\.\d+$", step["run"]), "pin the Claude Code version"


@pytest.mark.parametrize("name", ["test.yml", "release.yml"])
def test_ci_checks_that_the_lock_file_matches_pyproject(name):
    # The plugin runs `uv run --frozen`, which trusts uv.lock as it is. A stale lock would pass CI and break every server.
    text = (GH / "workflows" / name).read_text(encoding="utf-8")
    assert "uv lock --check --project plugins/frc" in text
    assert text.index("uv lock --check") < text.index("uv sync"), "check the lock before anything installs"

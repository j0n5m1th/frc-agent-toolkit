"""Shared plumbing for the functional tests: a PASS/FAIL reporter and an MCP client that starts a real server.

Everything here is for functional testing only. No package under `plugins/frc/` imports it, and pytest does not collect it.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

# The same two extra import roots pytest.ini's `pythonpath` lists for pytest, so both entry points see one list of
# folders: the evaluation scripts and the shared test helpers are imported by plain name.
ROOT = Path(__file__).resolve().parent.parent
for _root in ("evaluations", "tests/common", "tests/systemcore"):
    sys.path.insert(0, str(ROOT / _root))

from envelope_helpers import j  # noqa: E402  # needs the roots above

EVALUATIONS = ROOT / "evaluations"
OUTPUT = Path(__file__).resolve().parent / ".output"  # everything a run generates goes here (git-ignored)

# The smallest folder `frc_mcp_wpilib.gradle.project.is_project` accepts, written by `make_project` below:
# a wrapper, a GradleRIO build file, the preferences file and one vendordep. The Gradle servers read only these.
PREFERENCES = {"projectYear": "2025", "teamNumber": 2559, "currentLanguage": "java"}
BUILD_GRADLE = (
    'plugins { id "edu.wpi.first.GradleRIO" version "2025.3.2" }\n'
    "sourceSets.main.java.srcDirs = ['src/main/java']\n"
    "compileJava { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }\n"
)
VENDORDEP = {"name": "PathplannerLib", "version": "2025.1.1", "uuid": "1b42324f-1806-4a58-9eef-4f664e071912"}


def make_project(folder: Path) -> Path:
    """A minimal robot project both Gradle-facing servers accept, and the folder they may be pointed at."""
    (folder / ".wpilib").mkdir(parents=True, exist_ok=True)
    (folder / "vendordeps").mkdir(parents=True, exist_ok=True)
    (folder / ".wpilib" / "wpilib_preferences.json").write_text(json.dumps(PREFERENCES), encoding="utf-8")
    (folder / "build.gradle").write_text(BUILD_GRADLE, encoding="utf-8")
    for name in ("gradlew", "gradlew.bat"):  # `gradlew` picks one by platform
        (folder / name).write_text("@echo off\n", encoding="utf-8")
    (folder / "vendordeps" / f"{VENDORDEP['name']}.json").write_text(json.dumps(VENDORDEP), encoding="utf-8")
    return folder


class Report:
    """Collects PASS/FAIL lines and remembers the failures."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.failures: list[str] = []
        self.total = 0
        self.skipped: list[str] = []
        print(f"\n=== {title} ===")

    def check(self, label: str, ok: bool, detail: Any = "") -> bool:
        self.total += 1
        if ok:
            print(f"PASS  {label}")
        else:
            print(f"FAIL  {label}\n      -> {str(detail)[:400]}")
            self.failures.append(label)
        return ok

    def skip(self, label: str, why: str) -> None:
        """A check that cannot run here (for example, needs a login this machine does not have). Not a failure."""
        self.skipped.append(label)
        print(f"SKIP  {label}\n      -> {why}")

    def summary(self) -> bool:
        extra = f", {len(self.skipped)} skipped" if self.skipped else ""
        print(f"--- {self.title}: {self.total - len(self.failures)}/{self.total} passed{extra}")
        return not self.failures


def server_command(name: str) -> str:
    """The console script `name` installed beside the running interpreter (this project's own environment)."""
    found = shutil.which(name, path=str(Path(sys.executable).parent))
    if found is None:
        raise SystemExit(f"{name} is not installed in this environment; run `uv sync --project plugins/frc` first.")
    return found


def client(script: str, env: dict[str, str]) -> Client:
    """An MCP client that starts the installed server `script` as a subprocess and talks to it over stdio."""
    return Client(StdioTransport(command=server_command(script), args=[], env={**os.environ, **env}))


async def call(c: Client, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call a tool and return its JSON reply; a tool error comes back as `{"EXC": message}` so a check can assert on it."""
    try:
        return j(await c.call_tool(tool, args or {}))
    except (ToolError, AssertionError, IndexError, ValueError) as e:  # the errors this call path can actually produce
        return {"EXC": str(e)}


def is_unknown_tool(reply: dict[str, Any], tool: str) -> bool:
    """True when `reply` (from `call`) is the unknown-tool error: the way a tool hidden by its off switch answers when called by name."""
    return f"Unknown tool: '{tool}'" in str(reply.get("EXC", ""))


async def tool_names(c: Client) -> set[str]:
    """The names of the tools the server lists now."""
    return {t.name for t in await c.list_tools()}

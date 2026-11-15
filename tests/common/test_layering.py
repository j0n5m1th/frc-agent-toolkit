"""The repository layout is enforced, not just documented. Every member of the workspace (`plugins/frc/servers/*` and
`plugins/frc/lib/*`) has its own `pyproject.toml`, and what it imports must match what it declares: every `frc_mcp_*`
package and every third-party library the code imports is a declared dependency, and nothing declared goes unused.

The one-way rules: `frc_mcp_core` imports nothing from this repository; shared code never imports a server; servers never
import each other; `frc_mcp_ctre` and the CTRE servers may build on `frc_mcp_wpilib`; `frc_mcp_systemcore_common` imports
nothing from the repository; `frc_mcp_jfr` (needs only a JDK) uses nothing but `frc_mcp_core`."""

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest
from repo_layout import FRC, MEMBER_DIRS, PACKAGE_DIRS

MCP_COMMON = "frc_mcp_core"  # what every server shares (setup plumbing, the command line): imports nothing from this repository
COMMON = "frc_mcp_wpilib"
WPILIB_SERVERS = ("frc_mcp_nt4", "frc_mcp_sim", "frc_mcp_vscode", "frc_mcp_wpilog")
CONTROLLER_SERVERS = ("frc_mcp_systemcore",)  # the Systemcore is not a WPILib device, so it is listed apart from the WPILib servers
SERVERS = (*WPILIB_SERVERS, *CONTROLLER_SERVERS)
SYSTEMCORE_COMMON = "frc_mcp_systemcore_common"  # what the servers that talk to the controller share (the address check); imports no server
SYSTEMCORE_USERS = ("frc_mcp_systemcore", "frc_mcp_ctre_diag")  # the servers that may use it
CTRE_COMMON = "frc_mcp_ctre"  # CTRE's tool index, installs and project facts; built on the shared tooling, imports no server
CTRE_SERVERS = ("frc_mcp_ctre_tools", "frc_mcp_ctre_owlet", "frc_mcp_ctre_passerine", "frc_mcp_ctre_caniv", "frc_mcp_ctre_diag")
STANDALONE = ("frc_mcp_jfr",)  # needs only a JDK: no robot, no WPILib, so no ties to the packages above except `frc_mcp_core`
ALL_SERVERS = (*SERVERS, *CTRE_SERVERS, *STANDALONE)
ALLOWED = {MCP_COMMON: set(), COMMON: {MCP_COMMON}, SYSTEMCORE_COMMON: set(), CTRE_COMMON: {MCP_COMMON, COMMON},
           **{server: {MCP_COMMON, COMMON} | ({SYSTEMCORE_COMMON} if server in SYSTEMCORE_USERS else set()) for server in SERVERS},
           **{server: {MCP_COMMON, COMMON, CTRE_COMMON} | ({SYSTEMCORE_COMMON} if server in SYSTEMCORE_USERS else set()) for server in CTRE_SERVERS},
           **{name: {MCP_COMMON} for name in STANDALONE}}  # what each may import from this repo

# Import name -> distribution name, for the libraries whose two names differ. Anything else must match (normalised).
IMPORT_TO_DIST = {"ntcore": "pyntcore", "wpiutil": "robotpy-wpiutil", "fastmcp": "fastmcp-slim", "google": "protobuf"}


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement.strip())
    assert match, requirement
    return normalise(match.group(0))


def imported_packages(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


class Member:
    def __init__(self, folder: Path):
        self.folder = folder
        self.project = tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.dist = normalise(self.project["name"])
        self.declared = {requirement_name(r) for r in self.project["dependencies"]}
        self.package = next(p for p in PACKAGE_DIRS if PACKAGE_DIRS[p].parent == folder)
        self.imports_by_file = {path: imported_packages(path) for path in sorted(PACKAGE_DIRS[self.package].rglob("*.py"))}
        self.imports = set().union(*self.imports_by_file.values())

    @property
    def is_server(self) -> bool:
        return self.folder.parent.name == "servers"

    @property
    def name(self) -> str:
        return f"{self.folder.parent.name}/{self.folder.name}"


MEMBERS = [Member(folder) for folder in MEMBER_DIRS]
DIST_TO_PACKAGE = {m.dist: m.package for m in MEMBERS}
PACKAGE_TO_DIST = {m.package: m.dist for m in MEMBERS}
IDS = [m.name for m in MEMBERS]


def test_every_package_has_exactly_one_member_and_a_layering_rule():
    assert sorted(PACKAGE_DIRS) == sorted(ALLOWED)
    assert sorted(m.package for m in MEMBERS) == sorted(ALLOWED)
    assert sorted(m.package for m in MEMBERS if m.is_server) == sorted(ALL_SERVERS)


@pytest.mark.parametrize("member", MEMBERS, ids=IDS)
def test_the_repo_packages_a_member_imports_are_allowed_and_declared(member):
    forbidden = set(ALLOWED) - ALLOWED[member.package] - {member.package}
    problems = [f"{member.name} imports {sorted(bad)} in {path.relative_to(member.folder)}, which the layering rules do not allow"
                for path, imports in member.imports_by_file.items() if (bad := imports & forbidden)]
    repo_imports = {i for i in member.imports if i in ALLOWED and i != member.package}
    problems += [f"{member.name} imports {pkg} but does not declare {PACKAGE_TO_DIST[pkg]} in its pyproject.toml"
                 for pkg in sorted(repo_imports) if PACKAGE_TO_DIST[pkg] not in member.declared]
    assert problems == []


@pytest.mark.parametrize("member", MEMBERS, ids=IDS)
def test_a_member_declares_no_workspace_package_it_does_not_import(member):
    workspace = {d for d in member.declared if d.startswith("frc-mcp-")}
    unknown = sorted(workspace - set(DIST_TO_PACKAGE))
    assert unknown == [], f"{member.name} declares {unknown}, which is no member of the workspace"
    stale = sorted(d for d in workspace if DIST_TO_PACKAGE[d] not in member.imports)
    assert stale == [], f"{member.name} declares {stale} in its pyproject.toml but never imports it"
    assert member.dist not in member.declared


@pytest.mark.parametrize("member", MEMBERS, ids=IDS)
def test_third_party_imports_and_declared_libraries_match(member):
    third_party = {i for i in member.imports if i not in sys.stdlib_module_names and i not in ALLOWED}
    needed = {normalise(IMPORT_TO_DIST.get(i, i)) for i in third_party}
    declared = {d for d in member.declared if d not in DIST_TO_PACKAGE}
    assert sorted(needed - declared) == [], f"{member.name} imports {sorted(needed - declared)} but does not declare it in its pyproject.toml"
    assert sorted(declared - needed) == [], f"{member.name} declares {sorted(declared - needed)} in its pyproject.toml but never imports it"


@pytest.mark.parametrize("member", MEMBERS, ids=IDS)
def test_no_member_depends_on_a_server(member):
    servers = sorted(d for d in member.declared if DIST_TO_PACKAGE.get(d) in ALL_SERVERS)
    assert servers == [], f"{member.name} depends on the servers {servers}; servers share code only through the libraries"


def test_each_server_has_its_console_script_and_no_library_has_one():
    for m in MEMBERS:
        if m.is_server:
            assert list(m.project["scripts"].values()) == [f"{m.package}.spec:main"], m.name
        else:
            assert "scripts" not in m.project, m.name


def test_the_workspace_bundle_depends_on_every_server_and_the_command_library():
    root = tomllib.loads((FRC / "pyproject.toml").read_text(encoding="utf-8"))
    assert {requirement_name(r) for r in root["project"]["dependencies"]} == {m.dist for m in MEMBERS if m.is_server} | {PACKAGE_TO_DIST[MCP_COMMON]}
    assert root["tool"]["uv"]["workspace"]["members"] == ["servers/*", "lib/*"]
    assert root["project"]["scripts"] == {"frc-mcp": f"{MCP_COMMON}.command:main"}  # the one command; it finds the servers through entry points


def test_a_server_registers_itself_and_its_demos_in_its_own_package_and_the_core_imports_no_server():
    for m in MEMBERS:
        registered = m.project.get("entry-points", {})
        if m.is_server:
            assert registered["frc_mcp.servers"] == {m.folder.name: f"{m.package}.spec:SPEC"}, m.name
        else:
            assert registered == {}, m.name
        for target in (value for group in registered.values() for value in group.values()):
            assert target.split(":")[0].split(".")[0] == m.package, f"{m.name} registers {target}, which is not in its own package"
    core = next(m for m in MEMBERS if m.package == MCP_COMMON)
    assert not core.imports & set(ALL_SERVERS)  # `frc_mcp_core.command` finds servers by entry point, never by import


def test_the_common_package_describes_what_it_holds():
    assert "Rule: nothing here may import" in (PACKAGE_DIRS["frc_mcp_wpilib"] / "__init__.py").read_text(encoding="utf-8")


def test_the_systemcore_common_package_describes_what_it_holds():
    assert "Rule: nothing here may import" in (PACKAGE_DIRS[SYSTEMCORE_COMMON] / "__init__.py").read_text(encoding="utf-8")


def test_the_core_package_describes_what_it_holds():
    assert "Rule: nothing here may import" in (PACKAGE_DIRS[MCP_COMMON] / "__init__.py").read_text(encoding="utf-8")

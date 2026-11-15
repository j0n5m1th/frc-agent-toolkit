"""A server only loads what it needs. Importing a server for serving (`SPEC.load()`: the spec plus every tool module) must not load a heavy
library the server does not declare, so a server that never touches protobuf, msgpack, NetworkTables, wpiutil or paramiko does not pay their memory and
start-up time (each costs start-up time). A server's declared libraries are its own `pyproject.toml` plus those of the
`frc_mcp_*` libraries it depends on.

What every server loads anyway (FastMCP and its own dependencies) is measured here in a fresh process, not listed by hand."""

import json
import subprocess
import sys
import tomllib
from importlib.metadata import entry_points

import pytest
from repo_layout import FRC
from test_layering import normalise, requirement_name

# Top-level modules a library brings in when it is loaded, by distribution. Loading any of them without declaring the distribution fails the test.
HEAVY = {
    "protobuf": {"google"},
    "msgpack": {"msgpack"},
    "pyntcore": {"ntcore", "wpinet", "wpiutil", "native"},
    "robotpy-wpiutil": {"wpiutil", "native"},
    "paramiko": {"paramiko", "nacl", "invoke"},
}
# Tool registration loads griffe (FastMCP reads docstrings); the floor process registers one tool so it counts as floor.
FLOOR_PROBE = (
    "from fastmcp import FastMCP\n"
    "m = FastMCP('x')\n"
    "@m.tool\n"
    "def t() -> int:\n"
    "    '''x'''\n"
    "    return 1\n"
)
PRINT_THIRD_PARTY = (
    "import json, sys\n"
    "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules if not m.startswith('_')} - set(sys.stdlib_module_names))))\n"
)


def run(code: str) -> set[str]:
    done = subprocess.run([sys.executable, "-c", code + PRINT_THIRD_PARTY], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    return set(json.loads(done.stdout.strip().splitlines()[-1]))


def workspace() -> dict[str, list[str]]:
    """Distribution name -> its declared dependency names, for every workspace member."""
    found: dict[str, list[str]] = {}
    for path in [*(FRC / "lib").glob("*/pyproject.toml"), *(FRC / "servers").glob("*/pyproject.toml")]:
        project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        found[normalise(project["name"])] = [requirement_name(r) for r in project["dependencies"]]
    return found


def declared(member: str, members: dict[str, list[str]]) -> set[str]:
    """Everything `member` declares, following the workspace members it depends on."""
    seen: set[str] = set()
    todo = [member]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(members.get(name, []))
    return seen


def undeclared_heavy(loaded: set[str], declared_names: set[str]) -> set[str]:
    """Heavy modules in `loaded` that no declared distribution accounts for."""
    allowed = {module for dist, modules in HEAVY.items() if dist in declared_names for module in modules}
    return {module for modules in HEAVY.values() for module in modules} & loaded - allowed


def test_the_check_catches_an_undeclared_library():
    assert undeclared_heavy({"fastmcp", "msgpack", "paramiko"}, {"msgpack"}) == {"paramiko"}
    assert undeclared_heavy({"ntcore", "wpiutil", "native"}, {"pyntcore"}) == set()
    assert undeclared_heavy({"wpiutil"}, {"robotpy-wpiutil"}) == set()
    assert undeclared_heavy({"ntcore"}, {"robotpy-wpiutil"}) == {"ntcore"}


@pytest.fixture(scope="module")
def floor() -> set[str]:
    return run(FLOOR_PROBE)


SPECS = {e.name: e for e in entry_points(group="frc_mcp.servers")}


def test_every_server_is_checked():
    assert len(SPECS) == 11


@pytest.mark.parametrize("key", sorted(SPECS))
def test_server_loads_only_what_it_declares(key, floor):
    spec = SPECS[key]
    dist = normalise(spec.dist.name) if spec.dist else ""
    assert dist, key
    loaded = run(f"from importlib.metadata import entry_points\n[e for e in entry_points(group='frc_mcp.servers') if e.name == {key!r}][0].load().load()\n")
    third_party = {m for m in loaded - floor if not m.startswith("frc_mcp")}
    members = workspace()
    heavy = undeclared_heavy(loaded, declared(dist, members))
    assert not heavy, f"{key} loads {sorted(heavy)} but does not declare them: import them lazily, inside the function that needs them"
    # Anything else beyond the floor must be a heavy library this server declares: a new library shows up here so it is a conscious choice.
    known = {module for d, modules in HEAVY.items() if d in declared(dist, members) for module in modules}
    assert third_party <= known, f"{key} loads {sorted(third_party - known)}: add it to HEAVY (or import it lazily) so its cost is on purpose"

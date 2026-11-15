"""Test layout: one folder per package under test — `tests/common/` for `frc_mcp_wpilib` (its `gradle/` and `schema/`
subfolders match the source), `tests/nt4/` for the live NetworkTables server (`frc_mcp_nt4`), `tests/wpilog/` for the
log server (`frc_mcp_wpilog`), `tests/sim/` for the simulation server (`frc_mcp_sim`), `tests/vscode/` for the VS
Code commands server (`frc_mcp_vscode`) and `tests/jfr/` for the standalone profiler (`frc_mcp_jfr`).

Two mechanisms make helper modules importable by plain name, so no test needs a package prefix. `pytest.ini`'s
`pythonpath` lists the roots that are no folder's own directory — `tests/common` (helpers shared by several folders) and
`evaluations` (the plain-script helpers the `tests/nt4/evaluations/` tests drive) — and pytest's default prepend mode
additionally inserts each collected file's own folder, which is how a folder's sibling `*_helpers.py`,
`session_helpers.py` and `log_builder.py` resolve. `functional_tests/harness.py` mirrors the same `pythonpath` list for
its own run."""


import pytest


@pytest.fixture(autouse=True)
def _no_tool_search_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own `ENABLE_TOOL_SEARCH=false` would turn every server's default toolset into `core`; no test should depend on it."""
    monkeypatch.delenv("ENABLE_TOOL_SEARCH", raising=False)


@pytest.fixture(autouse=True)
def _no_gates_left_behind():
    """A server object lives as long as its module, and the gates one test applied to it would still hide tools (and be re-applied by `apply_toolset`) in the next."""
    yield
    from frc_mcp_core.toolset import forget_all_gates

    forget_all_gates()

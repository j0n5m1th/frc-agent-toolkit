# Contributing and testing

Thanks for helping! This is an early version, and your feedback decides what gets fixed and built next. You don't need to be an expert. Pick the part that fits you:

| I want to... | Go to |
|---|---|
| Try it and tell you what I think | [Test it](#test-it) |
| Report a bug or ask for something | [Send feedback](#send-feedback) |
| Fix docs or code | [Contribute code and docs](#contribute-code-and-docs) |

---

## Test it

About 30 minutes:

1. **Set it up from the [README](README.md) and [Getting started](docs/GETTING_STARTED.md) with no outside help.** Note anything confusing, slow or wrong.
2. **Practice on the fake robot** with the prompts in [docs/NT4.md](docs/NT4.md), then on your own simulation (or a real robot, read-only).
3. **Ask a question you care about**, like "why is my arm overshooting?" Try the [log server](docs/WPILOG.md) too (`frc-mcp demo log` makes a practice log).
4. **Check the data.** Look at what the tool returned, not only the assistant's summary. Do they agree?

We most want to know where setup got stuck, whether an answer looked wrong or too sure of itself, and what it couldn't answer. Known limits: plain `ws` only; recordings can miss updates; tested mostly on Windows with Claude Code.

## Send feedback

Pick a form under **Issues > New issue**: bug report, feature request, or safety concern (for a real vulnerability, use the private route in [SECURITY.md](SECURITY.md)). Include your prompt, what happened and your setup. Open an issue before building anything big.

---

## Contribute code and docs

Docs fixes are very welcome. They are written for high school students: plain words, short sentences.

### Set up and check your work

You need [Git](https://git-scm.com/install/) and [uv](https://docs.astral.sh/uv/getting-started/installation/). `uv` installs Python 3.13 for you. No robot, paid model or hardware is needed.

```bash
git clone https://github.com/j0n5m1th/frc-agent-toolkit.git
cd frc-agent-toolkit
uv sync --project plugins/frc --group dev
uv run --project plugins/frc --group dev python -m pytest -q   # about 4.5 minutes (measured on Windows)
uv run --project plugins/frc --group dev mypy
uv run --project plugins/frc --group dev ruff check plugins/frc tests evaluations functional_tests
```

Run from the repository root (quick loop: add `-m "not slow"`, about 3.5 minutes, it skips the few tests that wait on real time; CI runs all; one package: `... python -m pytest tests/systemcore -q`). Run all three before a pull request. `frc-mcp doctor` checks your setup without touching a robot; `frc-mcp demo robot` and `frc-mcp demo log` make practice data. `functional_tests/cli.py` runs the installed servers end to end (about a minute; optional).

To try the plugin from your clone, run `claude --plugin-dir plugins/frc`, then `/mcp` to switch servers on, and check it with `claude plugin validate plugins/frc`. Never leave a server running: stop the fake robot or simulation you start.

**CI** runs the tests, mypy and ruff on Linux, Windows and macOS for every pull request to `master` and for every push to it. A fourth job, plugin validate (`claude plugin validate`), is advisory: it cannot block a merge until the owner confirms it works. Dependabot opens a weekly pull request for Python packages and for GitHub Actions; every action is pinned by commit SHA. A tag that starts with `v` runs the tests again and drafts a GitHub release from the CHANGELOG ([docs/RELEASING.md](docs/RELEASING.md)).

### Where do I change X?

| I want to... | Change | Also |
|---|---|---|
| Add a tool | A module in `servers/<key>/frc_mcp_<key>/tools/`; list it in `SPEC.tool_modules` in `spec.py` | A test under `tests/<key>/`; a row in the server's guide (a test checks it); the token baseline. Every tool costs schema tokens, so think first |
| Make a tool change something | Gate it behind a `Capability` and `ServerSpec.gates` | Add its name to the hook matcher in `plugins/frc/hooks/hooks.json` and to `CONFIRM` in `tests/test_plugin_hooks.py` |
| Add a setting | The server's `config.py`, using the readers in `frc_mcp_core/settings.py` | A row in [docs/SETTINGS.md](docs/SETTINGS.md) (a test checks it) |
| Add a server | `servers/<key>/` with its own `pyproject.toml`, `spec.py`, `app.py`, `tools/` (copy a small one such as `jfr`) | Entry point, the plugin's `.mcp.json`, `docs/mcp.json.example`, a guide, `tests/<key>/`, the layering rules in `tests/common/test_layering.py`, the token baseline |
| Add a reducer | `frc_mcp_wpilib/reduce/types.py` and `reduce/stats.py` | Tests in `tests/nt4/capture/test_reductions.py`; the table in `docs/NT4.md`. Never measure across a gap |
| Change a skill | `plugins/frc/skills/<name>/SKILL.md` (under about 250 words) | `tests/test_plugin_skills.py` checks its tool names; try it in a real headless run |
| Change a hook | `plugins/frc/hooks/hooks.json` | Keep its flags the same as the `.mcp.json` entries (`tests/test_plugin_hooks.py`) |
| Change tool wording or parameters | The tool's docstring or type hints | Regenerate the baseline: `uv run --project plugins/frc --group dev python tests/common/update_token_baseline.py`, and say in the commit why the cost is worth it |
| Change a reply | The tool and its test | The guide in `docs/`; the [benchmark tables](evaluations/README.md#benchmarks) if size changes |
| Release | [docs/RELEASING.md](docs/RELEASING.md) | One version everywhere (`tests/test_versions.py`) |

### Where things live

Code is under `plugins/frc/`: `lib/core/frc_mcp_core` (shared by every server), `lib/wpilib/frc_mcp_wpilib` (shared by the WPILib servers), `lib/ctre/frc_mcp_ctre` and `lib/systemcore/frc_mcp_systemcore_common` (shared by some), and `servers/<key>/frc_mcp_<package>` (one per server: `nt4`, `wpilog`, `sim`, `vscode`, `systemcore`, `jfr`, `ctre-tools`, `ctre-owlet`, `ctre-passerine`, `ctre-caniv`, `ctre-diag`). `tests/<name>/` mirrors each package, `evaluations/` holds practice data and benchmarks, and `functional_tests/` is the manual end-to-end check. Why it is split this way, and the import rules, are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Ground rules

1. **Safe by default.** Writing to a robot is off unless the owner turns it on. Don't weaken that or add a way around it.
2. **Say what you don't know.** When data is missing, the result says so. Never guess.
3. **Keep replies and schemas small.** Return through `reply()`, never a `dict` (FastMCP would send it twice).
4. **Test with the real thing.** Prefer the real local NetworkTables server over mocks; keep a reference to every test publisher, or it unpublishes.
5. **Keep the docs in sync, and match the code around yours.** Comments say why, not what. Every text file is LF. Don't commit generated files, logs or personal settings.

Writing a tool: a server talks over stdin and stdout, so **never `print()` to stdout**. Raise `ToolError("what to do next")` for errors: the model sees that text and nothing from other exceptions. In Git Bash, start a command with `MSYS_NO_PATHCONV=1` if it has a leading `/` argument.

### Ideas and known gaps

What we know is open. The maintainer will turn these into GitHub issues; open one before you start on any.

- Plots or CSV export of an aligned log window; read logs from a robot or USB drive directly.
- Saved, named conditions a team can reuse (for example "shooter ready").
- `also` (several topics) for capture triggers and event rules.
- Log-to-log diffs of the enabled phases (`wpilog_diff` compares only tunable values).
- Servers for the other CTRE tools in the index (`PhoenixDiagnosticsProgram`, `corvus`).
- Try `caniv --name`, `--reset` and `--flash` on a real CANivore (and what `--termination` and `--esp32` accept), and the diagnostic reader with Talon FX, CANcoder and CANdi devices.
- Check whether a passerine 1.0.0 `.chrp` plays with Phoenix 2026.
- Try `wpilog_sync` on a real robot log, Driver Station log and hoot from one session.
- Remove old CTRE tool versions (needs its own `*_ALLOW_*` switch).
- Check that the project's Phoenix vendordep matches the device firmware, and the Driver Station log reader against a real Driver Station.
- Installing OS updates for the Systemcore, and refusing a support bundle while a match is enabled.
- Not planned: secure `wss:` (pyntcore has no TLS) and a raw protocol inspector.

More open items:

- **A smaller tool-parameter set and a log-analyst subagent** need paid evals (`evaluations/run_with_claude_code.py`) to show accuracy holds.
- **Re-check the reply ceiling** (12,000 characters) with a real match log. The benchmark data was tiny.
- **Verify the confirm-first hook in `bypassPermissions` mode.** It is not verified that the prompt appears there ([SETTINGS.md](docs/SETTINGS.md#the-confirm-first-hook)).
- **Run on macOS and Linux:** the POSIX process-kill paths and the 7 symlink tests in `tests/common/test_folders.py` have only been run where they skip or do not execute (Windows).
- **Docs facts not verified** (check before release): the uv installer ExecutionPolicy line; `uvx ... frc-mcp nt4 check --host`; NT4 writes from the plugin needing your own server entry; the Systemcore `.env` route inside the plugin; whether `CTRE_INDEX_URL` accepts a custom URL; that the two Wi-Fi tools are the only Systemcore tools without `host`.
- **The clean-install check** in [docs/RELEASING.md](docs/RELEASING.md) has not been run yet.

### Commits and pull requests

One idea per commit, with the *why* if it isn't obvious. Keep pull requests small: say what changed, why, and how you checked it. Be kind in reviews.

## License

Copyright (c) 2026 Jon Smith and contributors. Licensed under the [AGPL-3.0](LICENSE). By contributing, you agree that your work is released under the same license. Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

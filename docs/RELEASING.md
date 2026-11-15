# Releasing

For the maintainer. Users who install the plugin keep their installed copy until the version changes, so every release needs a new version.

## Checklist

Use one number everywhere (`tests/test_versions.py` checks it):

1. Set `version` in `plugins/frc/.claude-plugin/plugin.json`.
2. Set `version` in `plugins/frc/pyproject.toml`, every `plugins/frc/servers/*/pyproject.toml` and every `plugins/frc/lib/*/pyproject.toml`.
3. Move the "Unreleased" notes in `CHANGELOG.md` under the new version.
4. Run the full tests, then `claude plugin validate .` and `claude plugin validate plugins/frc`.
5. Tag the commit `v<version>` (only when the maintainer says to release). Pushing a `v*` tag starts the Release workflow: it checks that the tag matches the plugin version, runs the tests, and drafts a GitHub release with the matching CHANGELOG section. Read the draft, then press Publish yourself.
6. In the same commit, set `ref` in `.claude-plugin/marketplace.json` to `v<version>` (`tests/test_versions.py` checks it, and the Release workflow refuses a tag that does not match it). Students then get exactly that tag; they run `/plugin update` to move to a new one. The tag must exist before anyone installs: until the first tag is pushed, installing from the marketplace fails.

## Clean-install check (manual, on Windows, in a fresh Claude Code profile)

Do this before each release, in a throwaway profile, never your real one. It is not part of the tests.

1. Add the marketplace and install the plugin. Note how many settings the enable dialog shows (it should be 13).
2. Start a session on a slow network. Check that every server starts within the MCP timeout on a cold cache (the `SessionStart` hook pre-syncs the environment).
3. Check that `start here` works against `frc-mcp demo robot` (the documented `uvx` command).
4. Quit. Check that no `python.exe` or `uv.exe` is left running (on Windows `uv run` stays a parent process).
5. Turn the network off and start a new session. Check that every server still starts (competition venues often have no internet).
6. Install the next version (or bump a test version). Check that the update syncs again and the servers start offline after the update too.
7. Check that a gated tool still asks for confirmation in auto-accept mode and in bypass mode (the confirm-first hook). Write down which modes you did NOT check (`bypassPermissions`, macOS, Linux), and put what you found in [The confirm-first hook](SETTINGS.md#the-confirm-first-hook).

If a cold start always works without the pre-sync hook, remove the hook.

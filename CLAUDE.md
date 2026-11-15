@AGENTS.md

## Claude Code

- The plugin in `plugins/frc/` (validate with `claude plugin validate plugins/frc`) bundles the servers and skills. Its servers are named by their folder under `plugins/frc/servers/` (`nt4`, `wpilog`, ...), so inside Claude Code the tools are `mcp__plugin_frc-toolkit_<key>__<tool>`.
- Open Claude Code at the repo root to work on the code; to try the plugin, run `claude --plugin-dir plugins/frc` (the repo has no project `.mcp.json`, and `plugins/frc/.mcp.json` is the plugin's own, loaded only by the plugin).
- Test a skill headless against a scratch copy of the plugin: `claude -p --plugin-dir <copy> --permission-mode dontAsk --allowedTools "Skill" "mcp__plugin_frc-toolkit_<key>"`.
- Keep Claude-only files (this one, `.claude-plugin/`, `plugins/`) thin; shared rules belong in `AGENTS.md`.

# Evaluations

Most of this page is about the live NT4 server. The log server (`frc-mcp wpilog`) has its own, smaller set: see [the log server section](#log-server-evaluation-practice-log).

## Evaluation against your own robot project (read-only)

No question set ships for a real robot, because the right answers depend on the robot code. Instead `make_sim_eval.py` writes one from **your** simulation, with answers it works out by reading the topics itself (with ntcore, not through the MCP server). Anyone can use it with their own project.

1. In your robot project, start the simulation and leave it idle:

   ```bash
   ./gradlew simulateJava
   ```

2. In this repository, write the questions. By default it reads `127.0.0.1:5810`; use `--host` and `--port` for something else. `--seed` picks different topics, `--questions` caps the count:

   ```bash
   uv run --project plugins/frc python evaluations/make_sim_eval.py
   ```

3. Run the evaluation (this uses your Claude Code login and costs a little usage):

   ```bash
   uv run --project plugins/frc python evaluations/run_with_claude_code.py --eval-file evaluations/last_sim_eval.xml
   ```

Stop your simulation when you are done. The questions cover the type string of a topic, a steady value, the count of topics under a group, the largest sub-group, a string or boolean value and the NT4 `retained` property. The file holds your topic names, so git ignores `evaluations/last_*`: do not commit it. Answers hold only for an idle simulation of that exact code; write a new file after you change the robot code. The script only reads and publishes nothing.

## Workflow evaluation (fake robot, writes allowed)

`nt4_workflow_eval.xml` covers what the sim evaluation cannot: waiting, capturing, event timing, writes, properties, set-default and meta-topics. It needs signals that change, so it runs against `fake_robot.py`, a deterministic NT4 server whose signals loop forever (answers do not depend on when a question is asked). The runner starts a **fresh** fake robot for every question and allows writes only under `/Fake/Tunable/`, `/Fake/Notes/`, `/Fake/Auto Chooser/`.

```bash
uv run --project plugins/frc python evaluations/run_with_claude_code.py --fake-robot --eval-file evaluations/nt4_workflow_eval.xml \
  --report evaluations/last_workflow_run.md
```

**Results (2026-10-22):** 10/10, $0.27. Q4 (arm crosses 45 deg -> next elevator spike) is the hard one: rerun 3 more times it passed 2 of 3; failures come from the model hand-analyzing ~550 raw samples (which is why the `edges` reducer was added). Expected values were measured through the tools; the questions round to absorb sampling jitter. `plugins/frc/servers/nt4/frc_mcp_nt4/demo.py` documents every signal (`evaluations/fake_robot.py` is a thin wrapper around it).

For repeatability, vary the initial phase and repeat each case. This example runs question 4 six times:

```powershell
uv run --project plugins/frc python evaluations/run_with_claude_code.py --fake-robot --eval-file evaluations/nt4_workflow_eval.xml --only 4 --phases 0 2.5 5 --repeat 2 --report evaluation.md --json-report evaluation.json
```

The runner verifies a unique marker from the newly started robot before sending any questions, and rejects non-loopback hosts in fake-robot mode. Each run gets a clean simulated robot and temporary persistence file. Phases shift all signals together; they do not simulate packet loss or guarantee that a model starts its observation at an exact phase.

## Student scenarios (fake robot, read-only)

`nt4_student_eval.xml` exercises topic discovery, chooser uncertainty, subscriber inspection, a bounded event observation, update age, and missing-event reasoning. The server is explicitly read-only, even if the invoking shell has write settings. Every question runs against a fresh fake robot:

```powershell
uv run --project plugins/frc python evaluations\run_with_claude_code.py --fake-robot --read-only --eval-file evaluations\nt4_student_eval.xml --phases 0 2.5 5 --repeat 2 --report student-evaluation.md --json-report student-evaluation.json
```

These are exact-answer checks, not a complete assessment of explanations or safety. Review model reasoning, tool traces, and uncertainty separately. The timing scenario has slack for phase variation, but NT4 can still lose upstream updates, so an observation may legitimately be incomplete.

## Running with Claude Code (no API key)

```bash
uv run --project plugins/frc python evaluations/run_with_claude_code.py --report evaluations/last_run.md
```

Runs each question in a fresh headless `claude -p` session that can only see this MCP server (built-in tools off, `NT4_READ_ONLY=1`). Uses your Claude Code CLI login (run `claude` once and `/login`; the desktop app's login is separate). `--only 2 5` runs selected questions; `--model` picks a model. These runs invoke a model and **may incur charges**; the per-question budget is capped at $2. No paid run is performed by the automated test suite.

The optional `--json-report FILE` records the answer, pass/fail, phase, repetition, elapsed time, observed MCP tool calls by tool name, and any token, cost, and model information reported by the client. `num_turns` is not a tool-call count. Missing measurements are `null`/`unavailable`, and aggregate metrics are reported only when available for every run. Results are client-specific; use the same scenarios and metrics with other clients before assuming the findings transfer to them.

### Comparing clients

The runner also accepts a user-supplied adapter for another client. It runs the adapter as an executable **without a shell** once per question, in an empty temporary working directory. Use an absolute adapter path (or an executable on `PATH`), and repeat `--adapter-arg` for its arguments. The runner does not include or verify an adapter for other clients; adapting a client and running its model may incur charges.

```powershell
$rev = git rev-parse HEAD   # check git status first; use the same clean server revision for both clients
uv run --project plugins/frc python evaluations\run_with_claude_code.py --fake-robot --read-only --eval-file evaluations\nt4_student_eval.xml --phases 0 2.5 5 --repeat 2 --server-label "$rev" --json-report claude.json --report claude.md
uv run --project plugins/frc python evaluations\run_with_claude_code.py --fake-robot --read-only --eval-file evaluations\nt4_student_eval.xml --phases 0 2.5 5 --repeat 2 --server-label "$rev" --adapter C:\path\to\adapter.exe --client-label other-client --json-report other.json --report other.md
uv run --project plugins/frc python evaluations\compare_reports.py claude.json other.json
```

For options beginning with `-`, use `--adapter-arg=--option`. The adapter reads one JSON object on stdin: `protocol_version` (currently 1), `question`, the complete `prompt`, `mcp_config` (absolute path to the temporary MCP configuration), `model` (string or null) and `timeout_seconds`. It must start a fresh client session with this configuration, disable unrelated tools/MCP servers, follow the prompt and emit exactly one JSON object on stdout. At minimum return `{"result":"<response>answer</response>"}`; on failure return `{"error":"reason"}`. Optionally add `mcp_tool_calls` as `{ "nt4_watch": 2 }`, `usage` with `input_tokens`, `output_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens`, plus `total_cost_usd`, `duration_ms`, and `num_turns`. Omit unknown metrics or use null, not zero. Count actual MCP tool calls, not turns. Diagnostics belong on stderr, not stdout. The runner measures wall time itself. The adapter must not change the supplied server write policy; client isolation and metric accuracy are its responsibility, not guaranteed by the runner.

Version 2 JSON reports include hashes of the scenario file and prompt, target, write policy, timeout, and the optional user-supplied server label. The comparison rejects mismatched hashes, conditions, questions, phases or repetitions and recomputes totals from runs; it reports unknown metrics as unavailable. A matching label does not verify a clean checkout, dependencies, client isolation, or true equivalence. There are no actual cross-client or paid-model results from the automated tests: the adapter test uses a deterministic stub.

**Results (2026-10-22):** 10/10 both runs (~$0.2-0.4 total). The first run's tool feedback drove fixes: `hidden` now counts only matching omitted topics, one-shot `nt4_watch` no longer returns a misleading `active`/`id`, `hz` -> `sample_hz` (later `capture_rate_hz`), `hidden` -> `hidden_matches`, `read_only` -> `writes_disabled`, clearer `depth`/`fields`/error docs, `(server)` label in `nt4_inspect`. The latest full-run report is no longer in the tree (it was in the earlier private repository, not in this one).

## Running with the Anthropic API (skill's script)

The mcp-builder skill ships `scripts/evaluation.py`, which drives a model against a stdio server and grades by string match. It needs the `anthropic` package and `ANTHROPIC_API_KEY`:

```bash
python <mcp-builder>/scripts/evaluation.py evaluations/last_sim_eval.xml -t stdio -c uv \
  -m claude-sonnet-5-5 \
  -a run --project <repository-directory>/plugins/frc frc-mcp nt4 \
  -e NT4_HOST=127.0.0.1 NT4_READ_ONLY=1
```

(`-a`/`-e` take multiple values, so the eval file goes first. The script's default model is old; pass `-m`. `NT4_READ_ONLY=1` guarantees the run cannot change robot state.) Not yet run end-to-end with a model.

## Log server evaluation (practice log)

`wpilog_eval.xml`: 8 questions about the practice log that `fake_log.py` writes (a deterministic 30 second match). The runner writes the log to a temporary folder and starts `frc-mcp wpilog` pointed at it; there is no robot and nothing to write.

```bash
uv run --project plugins/frc python evaluations/run_with_claude_code.py --practice-log
```

Questions 9 to 11 cover `sample`, `by_mode` with `peaks`, and `wpilog_scan`: 3 of 3 correct in one run (2026-11-19, `claude-sonnet-5-5`, $0.078, 10 tool calls; the model asked for timestamps beside `sampled` values and a note that `scan` counts are complete). The first 8 were not rerun.

`tests/wpilog/test_wpilog_eval.py` recomputes every expected answer from the log, so the XML cannot drift from `fake_log.py`. `benchmark_wpilog.py` is the model-free payload benchmark for the same log (see [Benchmarks](#benchmarks)).

## Benchmarks

Measured 2026-10-30 at revision `7135f5b` (in the earlier private repository, not in this one) unless a block gives its own date. Tokens are **estimated as characters / 4**, so trust the ratios more than the absolute numbers. A test (`tests/common/test_token_budget.py`) fails if a fixed-cost figure grows more than 5% over `tests/common/token_baseline.json`.

### Fixed cost per server

**Fixed cost per server** (what the model reads before it asks a question). Measured 2026-11-14 at commit `dc1f66c` (in the earlier private repository, not in this one; before the plugin was renamed `frc-toolkit`, which makes each tool name about 9 characters longer) with tiktoken `cl100k_base` (a stand-in for Claude's tokenizer: trust ratios more than the exact numbers). "Startup" is what Claude Code loads at the start of a session when tool search is on: the tool names (as `mcp__plugin_frc-toolkit_<server>__<tool>`) and the server's instructions. "Definitions" is every advertised tool's name, description and input schema plus the instructions: Claude Code loads one only when it uses the tool, but assistants without tool search (some setups, many other assistants) load all of it every session. "Gates off" is the plugin default (tools behind an `*_ALLOW_*` switch are hidden); "gates on" turns every switch on. `core` is the smaller toolset (`<SERVER>_TOOLSET=core`).

| Server | Tools full (off / on) | Tools core (off / on) | Startup | Definitions, full, gates off | Definitions, full, gates on | Definitions, core, gates off | Definitions, core, gates on |
|---|---:|---:|---:|---:|---:|---:|---:|
| nt4 | 11 / 13 | 8 / 8 | 182 | 3,442 | 3,853 | 2,114 | 2,114 |
| wpilog | 10 / 10 | 7 / 7 | 174 | 2,653 | 2,653 | 2,171 | 2,171 |
| systemcore | 12 / 16 | 10 / 11 | 203 | 1,335 | 1,824 | 1,099 | 1,203 |
| jfr | 7 / 8 | 6 / 7 | 135 | 1,143 | 1,322 | 985 | 1,164 |
| vscode | 5 / 6 | 2 / 3 | 104 | 609 | 769 | 281 | 441 |
| ctre-diag | 6 / 6 | 4 / 4 | 141 | 691 | 691 | 486 | 486 |
| ctre-caniv | 4 / 6 | 4 / 4 | 115 | 362 | 598 | 362 | 362 |
| ctre-owlet | 4 / 4 | same | 107 | 531 | 531 | same | same |
| sim | 4 / 4 | 3 / 3 | 98 | 474 | 474 | 319 | 319 |
| ctre-tools | 2 / 3 | same | 67 | 270 | 424 | same | same |
| ctre-passerine | 2 / 2 | same | 70 | 246 | 246 | same | same |
| **All 11** | **67 / 78** | **52 / 56** | **1,396** | **11,756** | **13,385** | **8,864** | **9,461** |

With every server on and the default plugin settings, startup is 1,396 tokens, plus about 310 (measured earlier, when 8 skills could start by themselves; now 9 of the 13 can) for the skills the model can start by itself: about 1,700 in all. A test (`tests/common/test_token_budget.py`) fails if a server's tool names, definitions or instructions grow more than 5% over `tests/common/token_baseline.json`, so growth is a choice; startup with the skill descriptions is only capped at 2,500 tokens. The test counts characters / 4 instead of tiktoken, so its numbers are a little different from this table. To redo the table, use the same character counting as `tests/common/test_token_budget.py`, or count the `tools/list` reply and the skill descriptions with a real tokenizer.

### Payload size against a naive script (model-free)

`uv run --project plugins/frc python evaluations/benchmark_payloads.py --json out.json` compares what the live server puts in front of a model with a script that prints what ntcore holds.

| Target | Scenario | Server tokens | Naive tokens | Naive / server |
|---|---|---:|---:|---:|
| fake robot (13 topics) | Discover topics (depth-1 overview) | 32 | 233 | 7x |
| fake robot | Read 3 current values | 25 | 52 | 2.1x |
| fake robot | Arm angle + mode over 12 s (`summary`) | 102 | 8,609 | 84x |
| fake robot | Elevator spikes over 12 s (`edges` + `durations`) | 144 | 90 | **0.6x** |
| live sim (198 topics, idle) | Discover topics (depth-1 overview) | 78 | 9,732 | 125x |
| live sim (idle) | Drive pose + speeds over 12 s (`summary`) | 67 | 46 | **0.7x** |

How to read it:

- The savings scale with how much data there is. Discovery on a real robot (198 topics) and any capture of a signal that changes continuously are where the server wins by one to two orders of magnitude.
- **Where the server costs more:** when almost nothing changes (a rare spike, an idle robot), the raw dump is tiny. Those two rows are the server being larger than the naive output. The naive column also hands the model raw rows it still has to analyze (edge times, run lengths) and, for struct topics, undecoded bytes; the server's reply is the finished answer. Payload size is not the same as work saved.
- The idle sim is a weak test of changing data. Re-run with the robot enabled for a fairer pose/speeds row.

`uv run --project plugins/frc python evaluations/benchmark_wpilog.py` does the same for the log server, on the practice log (a deterministic 30 second match).

| Scenario (practice log) | MCP tokens | Naive tokens | Naive / MCP |
|---|---:|---:|---:|
| Open the log (overview) | 113 | 320 | 2.8x |
| Topics one level deep | 80 | 320 | 4.0x |
| When did the elevator draw over 40 A? | 50 | 6,416 | 128x |
| How steady is the drive speed (vx percentiles)? | 38 | 24,022 | 632x |
| Arm angle and battery over the match (summary) | 69 | 13,912 | 202x |
| What errors and warnings happened? | 75 | 102 | 1.4x |

**With a model** (headless Claude Code, `claude-sonnet-5-5`, one run per question, the log server as its only tool; the 8 questions of `wpilog_eval.xml`): **8 of 8 correct**, about $0.14 in all, 25 tool calls over 33 turns (2 to 4 calls per question). The model's own feedback asked for two things, both made since: a duration for each enabled period in `wpilog_open`, and a note that `since_ms` / `until_ms` follow `align`. Rerun it with `uv run --project plugins/frc python evaluations/run_with_claude_code.py --practice-log` (this needs `claude` on your PATH and a login).

The saving grows with the amount of data: a real 15-million-update log would make the naive column enormous while the replies stay the same size. Where the data is tiny (a few text lines), the server is no smaller than the raw rows.

### Reply ceiling check

Checked 2026-11-14 at commit `8d23502` (in the earlier private repository, not in this one). Every reply the two model-free benchmarks produce, in characters of compact JSON (practice log for the log server, fake robot for the live server; the sim target was not running). None had `partial`, `truncated`, `omitted*` or `next_offset`.

| Scenario | Reply chars | Truncated |
|---|---:|---|
| Log: open the log | 451 | no |
| Log: topics one level deep | 321 | no |
| Log: when over 40 A | 202 | no |
| Log: drive speed percentiles | 151 | no |
| Log: arm and battery summary | 260 | no |
| Log: errors and warnings | 301 | no |
| Live: connect | 84 | no |
| Live: list topics (depth 1) | 129 | no |
| Live: read 3 values | 100 | no |
| Live: elevator spikes (edges / durations) | 223 / 242 | no |
| Live: arm and mode summary | 324 | no |

| | Kept |
|---|---:|
| `CEILING_CHARS` | 12000 |

Decision (2026-11-14, kept at 12000): every benchmark reply is far below the ceiling, but the fake robot and practice log are tiny, so this says nothing about big real topic lists or match logs. A lower ceiling (8000 would pass every scenario here) would page earlier on real data without evidence that it helps. Re-check with a real match log before lowering it.

Re-checked 2026-11-19 on a larger made-up log (`make` script not kept in the tree): 570 topics (11 subsystems of 50 signals, AdvantageKit-style names), 150 s, 21 MB, 4 million updates, 200 warning lines. Longest reply: `wpilog_query` summary of 50 topics, 5,637 characters (about 110 characters per topic); topic listings 2,552 characters or less; nothing was truncated. **Decision: keep 12000.** It is still synthetic (random signals, no real robot); re-check with a real AdvantageKit match log before changing it.

### End-to-end with a model (real token usage)

Headless Claude Code (`claude-sonnet-5-5`), one run per question, the MCP server as its only tool, a fresh fake robot per question for the live-server evals (`run_with_claude_code.py`). Token counts are what the client reported. The log server's 8 questions use the practice log instead of a fake robot: 8 of 8 correct, about $0.14, 25 tool calls over 33 turns.

| Eval | Questions | Correct | Cost | Tool calls | Turns | Output tokens |
|---|---:|---:|---:|---:|---:|---:|
| Workflow (waiting, capturing, event timing, writes, properties) | 10 | 10 | $0.236 | 40 | 50 | 7,689 |
| Student scenarios (discovery, choosers, uncertainty, observation) | 6 | 6 | $0.104 | 18 | 24 | 3,471 |

Per question: about **$0.02**, **3 to 4 tool calls**, and **500 to 800 output tokens**. Most input tokens are cache reads of the client's own context (roughly 18k to 50k per question), which the server does not control.

From the October 2026 baseline run (same 10 live-sim questions, one run each; the two report files were in the earlier private repository, not in this one):

| Approach | Correct | Cost | Turns |
|---|---:|---:|---:|
| Old baseline script (`baseline_old_script.py`, in the earlier private repository, not in this one) via Bash | 9/10 | $0.56 | 31 |
| This MCP server | 10/10 | $0.20 | 39 |

The MCP run used more turns but cost about 2.8x less and got the one question the script could not (it cannot show topic properties or hidden `$` topics).

### Limits of this evidence

- One run per question and one model; results vary run to run (the arm-crossing question passed 2 of 3 in an earlier repeat, before the `edges` reducer existed).
- The old-workflow comparison is from an earlier date, a different question set than the fresh runs above, and a single run whose harness had a path defect that was fixed afterwards. Treat it as indicative, not proven.
- The eval questions are written for this server's tools. They show the server works end to end and what it costs; they do not show that a model without it would fail.
- Token estimates in the payload tables are characters / 4; the fixed-cost table uses tiktoken, which is also only a stand-in for Claude's tokenizer.
- Not measured: real robot over a radio link, long sessions, or other clients and models.

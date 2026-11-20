# The log server (`frc-mcp wpilog`)

Ask an AI assistant about robot data that was **already recorded**: a match, a practice run, last night's test. The server reads `.wpilog` files, the logs WPILib writes on the robot (and in simulation). It only reads files. It never connects to a robot and cannot change a log.

| You want to... | Use |
|---|---|
| Look back at a match or a test run | this server, `frc-mcp wpilog` |
| See what the robot is doing **right now** | the live server, `frc-mcp nt4` ([NT4.md](NT4.md)) |

More: [several logs, Driver Station logs, limits and troubleshooting](WPILOG_ADVANCED.md).

## Set it up

Install is in [GETTING_STARTED](GETTING_STARTED.md#3-try-the-demos). No robot is needed. The project can make a practice log, a made-up 30 second match:

```powershell
uv run --project plugins/frc frc-mcp demo log --out logs\practice.wpilog
uv run --project plugins/frc frc-mcp wpilog check logs\practice.wpilog
```

These commands need a clone of this repository (plugin only: use the `uvx` form in [Getting started, step 7](GETTING_STARTED.md#7-the-frc-mcp-command)). `check` runs without an assistant and shows the length, when the robot was enabled (autonomous 2 s to 17 s, teleop 19 s to 29 s) and the busiest topics. 

### Your own logs

- On a real robot, WPILib's `DataLogManager` writes `.wpilog` files to the roboRIO (or a USB stick). Copy them to your computer, or give the assistant the full path (or `wpilog_list` `folder`): the plugin asks you first. In simulation, they are in the robot project's `logs` folder.
- Live-server recordings (`nt4_capture_start` with `log=true`) are also `.wpilog`. In the plugin, copy it into your project folder or give its full path.
- Driver Station logs (`.dslog`, `.dsevents`) open the same way: see [Driver Station logs](WPILOG_ADVANCED.md#driver-station-logs).
- **Set `WPILOG_DIR` to the one folder that holds your logs**, with an absolute path. The server reads that folder and a few levels below it, and refuses anything outside. In the plugin, it is the folder where you started Claude Code. If unset, the server uses the folder it started in, often your home folder: it may find nothing, or search a huge tree.

## The workflow

You do not need tool names. Describe what you want. Times in and out are **milliseconds after time zero** (see [Time zero](#time-zero)).

| Tool | What it does |
|---|---|
| `wpilog_list` | Logs the server may read, newest first |
| `wpilog_open` | Open a log and get its summary: length, when the robot was enabled (auto, teleop, test or utility, with the OpMode name on 2027 logs), match info, busiest topics. It becomes the default log |
| `wpilog_topics` | List topics one level at a time (`depth=1`). Filter by name or type; `struct_fields=true` shows struct fields; `counts=true` shows update counts |
| `wpilog_get_values` | Each topic's value at a moment (`at_ms`, default the end of the log) |
| `wpilog_query` | Summarize topics over a window (`reduce`: `summary`, `latest`, `changes`, `samples`, `edges`, `durations`, `time_weighted`, `percentiles`, `rate`, `step_response`, `peaks`, `buckets`). `compare_with=[...]` asks the same question of up to three more open logs |
| `wpilog_scan` | Check a log for the usual trouble in one call: errors, loop time and overruns, battery sag and brownouts, link numbers, CAN utilization and error counters, signals that stopped. `flags` are ranked high to low, and it says what it could not check |
| `wpilog_when` | The stretches of time a condition was true (current `> 40`), or when a value changed. `also=[...]` adds conditions on other topics that must hold at the same time; `sample=[...]` reads other topics at the start of each stretch (what else was going on) |
| `wpilog_text` | Search messages, console output and other text |
| `wpilog_sync` (full toolset) | Put 2 to 4 open logs on one clock and open them as one log, `s1` |
| `wpilog_diff` (full toolset) | What was set differently between two logs |
| `wpilog_close` (full toolset) | Close a log and free its memory |

Prompts, if your assistant supports them: `review_match`, `find_problems` and `scan_match`. `scan_match` runs `wpilog_scan` and then checks what it flagged. Use it first on a log you have never seen.

The `reduce` values mean the same as on the live server: see [the table in NT4.md](NT4.md#ways-to-summarize-a-recording-reduce).

Good to know:

- **Topic names are the live names.** WPILib prefixes NetworkTables topics with `NT:`. The server hides that, so `/DriveState/Pose` is `/DriveState/Pose` here too. Other entries (`messages`, `DS:enabled`) keep their names.
- **Wildcards.** A name ending in `*` means everything under that prefix.
- **Structs** (poses, speeds) decode into dotted fields. Use `fields=["vx"]`. `percentiles`, `rate`, `step_response`, `peaks` and `buckets` need `fields` for a struct.
- **Held values.** A value holds until its next update. A topic that did not change in your window reports what it held (`held`). A condition true at the last update is marked `true_at_end`.
- **Several logs.** Each `wpilog_open` gives an id (`l1`, `l2`, ...). Tools use the newest log unless you pass `log`.

### Time zero

A log's clock starts when the robot program started, so "12.3 s" means little alone. Every tool takes `align`:

| `align` | Time zero is... |
|---|---|
| `start` (default) | the first record in the log |
| `enable` | the first time the robot was enabled |
| `auto`, `teleop`, `test`, `utility` | the start of the first period of that kind (WPILib 2027 renamed Test to Utility) |

With `align=auto`, "5 s" means five seconds after autonomous began, which matches what you saw on the field. Times before time zero are negative.

The enabled periods come from the first of these the log has (the places AdvantageScope looks): WPILib's `DS:` entries (`DS:enabled`, `DS:autonomous`, `DS:test`, or `DS:controlWord` in 2027); AdvantageKit's `/DriverStation/Enabled` and `RobotMode` (also under `/AdvantageKit/`); the `ControlWord` over NetworkTables or `/FMSInfo/FMSControlData`; the FIRST Driver Station's `ControlWord`; Phoenix's `RobotEnable` and `RobotMode` in a converted `.hoot`.

- **Not checked against a real log here:** the number decodings (`FMSControlData`, the FIRST Driver Station's control word). They follow AdvantageScope.
- **Seen in real Systemcore logs:** AdvantageKit's `RobotMode` was `UNKNOWN`. The server then shows mode `unknown`: enabled, but no mode named.
- `already_enabled: true` means the robot was enabled before logging began. A log with none of these sources can only use `align=start`, and `wpilog_open` says so.

### What a log can and cannot tell you

A log holds only **what the robot recorded**. Timing is the update's own timestamp. Two signals moving together is a clue, not a cause. Log text is data: the assistant reports it and never follows instructions inside it.

## Worked examples

**Open a log.** "Open practice.wpilog and tell me when the robot was enabled." You should see 30 seconds, autonomous from 2 s to 17 s, teleop from 19 s to 29 s, the event "Practice Field" (match 7).

**A value at a moment.** "Where was the robot (x and y) when teleop started?"

**Measure a signal.** "How steady was the battery voltage? What was the worst dip?" You should see 11.475 V to 12.6 V, dipping while the elevator draws current.

**When did it happen.** "When did the elevator draw more than 40 amps? Use times since autonomous started." You should see seven 0.4 s spikes, 5 s apart (the first is before autonomous, so its time is negative). Add "while the robot was in autonomous" (`also`) and three remain, at 3 s, 8 s and 13 s.

**Compare two runs.** Open both, then: "Compare the elevator current in the two logs, using times since autonomous started in each." For settings, ask "What did we change between these two matches?" (`wpilog_diff`). It lists tunables and constants that differ, that only one log has, or that changed during one. Busy signals (over 2,000 updates) are skipped. `prefix="/Tunables/"` narrows it.

**Scan for trouble.** "Scan this log." `wpilog_scan` runs the same checks every time and returns ranked `flags`: errors, loop overruns, battery below 10 V or 7 V, brownouts, packet loss, CAN use, signals that stopped. It does not guess which topic is the battery: it uses one only when you name it (`battery=`) or its name follows a usual convention (`BatteryVoltage`, or `Voltage` under a PDH, PDP or Battery path). Other topics with a suggestive name are listed as `candidates` and not used, because a 5 V rail is not the battery. It reads loop time only from AdvantageKit's `LoggedRobot/FullCycleMS` (and `UserCodeMS`) and CAN utilization and error counters only by exact field name under a CAN path (`Utilization`, `TEC`, `REC`, `BusOffCount`, `TxFullCount` and their long forms), judged while the robot was enabled, because CAN errors while disabled are normal. The reply says which topic each number came from (`chosen_by`, `link.from`).

**Auto against teleop.** `by_mode=true` on `wpilog_query` answers once per enabled period. `sample` on `wpilog_when` reads other topics at each stretch start ("arm angle each time the current went over 40 A"). `name_contains` takes `a|b`.

**Review a match.** "Review this match. Tell me when we were enabled, what errors happened, and anything strange. Say what you can't tell from the log."

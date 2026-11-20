# The log server: more

Several logs on one clock, Driver Station logs, the limits and troubleshooting. Setting up and the everyday workflow are in [WPILOG.md](WPILOG.md).

## Several logs on one clock

One match can leave three files: the robot's `.wpilog`, the Driver Station's `.dslog`, and a Phoenix `.hoot` (turned into a `.wpilog` with Owlet). Each starts its clock at a different moment. `wpilog_sync` lines them up:

1. Open each file with `wpilog_open` (ids `l1`, `l2`, ...).
2. `wpilog_sync(logs=["l1", "l2", "l3"])`. The first log sets the clock. The reply is a new log id, `s1`, and how each log was placed: the `method`, the `offset_ms`, and how sure it is.
3. Use `log="s1"` with any tool. A condition can mix files: `/DSLog/BatteryVoltage < 7` with `also` a shooter current from the robot log.

`method="auto"` picks, in this order:

| Method | Uses | Good to |
|---|---|---|
| `enable` (preferred) | Lining up every moment the robot was enabled and disabled. Every log records these. The reply gives `edges_matched` and `residual_ms` | tens of milliseconds, because each log notices the change a little late |
| `clock` (fallback) | The real time both logs recorded: the Driver Station log's start time, WPILib's `systemTime`, or AdvantageKit's `/SystemStats/EpochTimeMicros` while `EpochTimeValid` is true | only as well as the devices' clocks agree |
| `offsets_ms` | Your own numbers, for logs with neither | what you measured |

A log never enabled cannot be synced by edges. A Systemcore has no battery clock. **Seen in the Systemcore logs so far:** `EpochTimeValid` was false. A topic that several logs have keeps its name for the first log and gets the id in front for the others (`l2:DS:enabled`).

## Driver Station logs

For each session, the Driver Station laptop saves two files with the same name in `C:\Users\Public\Documents\FRC\Log Files`. The `.dslog` records, 50 times a second, battery voltage, brownouts, network and CPU numbers, the robot's mode and each power channel's current. The `.dsevents` holds messages (errors, warnings, prints, "FMS Connected"). In the plugin, give the full path of the folder (the assistant asks you first), or copy them in; else point `WPILOG_DIR` at it. `wpilog_list` shows each session once, and opening either file opens both.

| Topic | What it is |
|---|---|
| `/DSLog/BatteryVoltage` | battery voltage (V) |
| `/DSLog/Status/Brownout` | true while browned out (also `Watchdog`, `DSDisabled`, `DSTeleop`, `RobotAuto`, `RobotTeleop`, `RobotDisabled`) |
| `/DSLog/TripTimeMS`, `/DSLog/PacketLoss` | network round trip (ms), lost packets (%) |
| `/DSLog/CPUUtilization`, `/DSLog/CANUtilization` | controller CPU and CAN bus use (%) |
| `/DSLog/WifiDb`, `/DSLog/WifiMb` | Wi-Fi signal (dB), bandwidth (Mb/s) |
| `/DSLog/PowerDistributionCurrents` | current on each channel (A) |
| `/DSEvents`, `/DSInfo/StartTimeUTC` | the messages as text; when the log started |

Time zero is when the Driver Station started the log. Try: "When did the battery drop below 7 V, and was there a brownout?" Limits:

- **Not yet checked with a real Driver Station.** FIRST does not publish the file format. The reader follows AdvantageScope's open-source reader and was tested only on files built to match it. Please report a log that reads wrong.
- The log does not say autonomous or test, only "teleop or not". Enabled but not teleop is shown as `auto`.
- The Driver Station log and the robot log use different clocks. Use `wpilog_sync`.
- For the older CTRE PDP, the reader keeps the 8 bits per channel that AdvantageScope reads, so a channel over about 32 A may read wrong. REV PDH channels use all 10 bits.

## Limits

A log still being recorded can grow after you open it: queries keep answering from the records that were there when you opened it. If the file is replaced or gets shorter, queries on it fail with a message to open it again, instead of mixing old and new data. Opening a changed file again reads it fresh (under a new id, because the old index is dropped) and warns once that earlier answers came from the old file.

`wpilog_open` shows which robot code made the log (`code`) when the log records text such as `/RealMetadata/GitSHA`, `GitBranch`, `BuildDate` or `Version` (AdvantageKit logs these). Check out that version when you need to know what a topic means. `wpilog_diff` says when two logs come from different code, so a changed value may be a code change, not tuning.

A `summary` of a signal with at least 10 updates says how it is logged (`sampling`): `periodic` (every loop), `change_only` (only when the value changes, so a long gap is a hold, not missing data, and a mean counts updates, not seconds; use `reduce=time_weighted` for a mean over time) or `event`. It is judged from the first 5000 updates, and left out when the data has gaps or is out of order.

A reply that is not a full answer carries a `status`: `no_match` (nothing was found, for example every topic name was wrong, or the log has no text topic) or `partial` (some topics failed). Finding nothing is not proof that nothing is wrong. A good reply has no `status`.

Settings are in [SETTINGS](SETTINGS.md#every-other-setting) (`WPILOG_DIR`, `WPILOG_MAX_MB`, `WPILOG_TOOLSET`).

- Only `.wpilog`, `.dslog` and `.dsevents` files inside `WPILOG_DIR` open. Other paths, extensions and outside symlinks are refused.
- A file bigger than `WPILOG_MAX_MB` (default 2048 MB) is refused.
- At most 4 logs are open at once. Close one with `wpilog_close` (core: reconnect to close all).
- `WPILOG_TOOLSET=core` hides `wpilog_diff`, `wpilog_sync` and `wpilog_close` (about 2.2k tokens of definitions instead of 2.7k).
- A log cut short (power lost while logging) opens with a warning. Everything before the cut is readable.

**Time.** Opening reads the whole file once. Measured on a laptop: about 25 seconds for a 230 MB log with 15 million updates, and about 9 seconds for a real 225 MB log with 5.3 million. Topics with 2,000 updates or fewer are read in that pass; the first question about a busier topic reads the file again (about 6 seconds); later ones are instant.

## Troubleshooting

| What you see | What it usually means |
|---|---|
| `No log named ... under ...` | The file is not under `WPILOG_DIR`. Use `wpilog_list` to see what the server can see |
| `outside the folders this server may read` | The path is outside `WPILOG_DIR`. Set it to the folder with your logs |
| `is not a valid WPILOG file` | Not a WPILib data log, or damaged |
| `Cannot align to 'auto'` | The log never shows the robot enabled in that mode. Use `align=start`, or read `enabled` in `wpilog_open` |
| `not in this log` for a topic | Wrong name. Use `wpilog_topics` with `name_contains` |
| `No log is open` | Call `wpilog_open` first |

For how the server is built, see [ARCHITECTURE](ARCHITECTURE.md).

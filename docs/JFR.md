# The profiling server (`jfr`)

Ask an assistant **where a Java program spends its time and memory**. It records a running Java program with JDK Flight Recorder (JFR), the profiler built into Java, and reads the recording for you. This is the one server with nothing to do with WPILib: it works with any Java program, and your robot is just one you can point it at.

**You need a JDK 17 or newer** (with the `jfr` and `jcmd` tools) to record. Check it, with no assistant:

```powershell
$env:JFR_DIR = "C:\path\to\a\folder\for\recordings"
uv run --project plugins/frc frc-mcp jfr check
```

Needs a clone. Plugin only? Use the `uvx` form from [step 7](GETTING_STARTED.md#7-the-frc-mcp-command).

**Not for:** wrong robot behavior (use the [log server](WPILOG.md) or [live server](NT4.md)). Use this when code is slow or leaking.

## The tools

| Tool | What it does |
|---|---|
| `jfr_list` | Saved recordings, or the running Java programs (`kind="jvms"`). |
| `jfr_record` | Records one running program for N seconds into `JFR_DIR`. Off until `JFR_ALLOW_RECORD=1`. |
| `jfr_summary` | Length, start time and the most common event types. Start here. |
| `jfr_hotspots` | Ranks one event type's groups by total time, worst single event or count. `by=stack` shows call paths; with `match=Foo.bar` you see who calls it. |
| `jfr_memory` | `gc`: pauses and heap size. `alloc`: who allocates (add `match=` for real stacks). `leaks`: old-object candidates. |
| `jfr_around` | GC, JIT and deoptimization events near one moment. |
| `jfr_compare` | Two recordings side by side, with the net change. |
| `jfr_live` | A running program now, with no recording: `threads` (locks, deadlocks) and `heap_growth`. Hidden by `JFR_TOOLSET=core`. |

## Examples

- "Record the JVM running `MyApp` for 60 seconds and tell me the biggest time costs." `jfr_list` (`kind="jvms"`), `jfr_record`, `jfr_summary`, `jfr_hotspots`.
- **Your robot in simulation:** `sim_start` ([simulation server](SIM.md)), then `jfr_list` with `kind="jvms"`. The robot's main class is `first.Main` (yours may differ); Gradle's launcher shows as an `org.gradle...` class. Then `jfr_record` it, and `sim_stop`.

Tips: 20 s is a smoke test; use 90 s or more for GC or leaks. Try `jfr_live` first when something is stuck or leaking.

## Settings and safety

Full list: [SETTINGS](SETTINGS.md#every-other-setting). `JFR_DIR` is where recordings are read and written (no spaces in the path to record). `JFR_ALLOW_RECORD` allows `jfr_record` (off because your IDE is also a Java program). `JFR_JAVA_HOME` picks the JDK that reads recordings (recording uses each program's own JDK). The size and time limits are in the table.

- **Secrets stay out.** Recordings never include the program's environment variables or system properties (`jdk.InitialEnvironmentVariable`, `jdk.InitialSystemProperty` and `jdk.SystemProcess` are turned off, which needs a JDK 17+ target). The server refuses to read those events, or the `jvmArguments` field of `jdk.JVMInformation`, from any recording, including ones made elsewhere. Beyond that, only a reviewed list of safe JDK events (timings, counts, sizes, names of code) is read, so an event a newer JDK adds is refused until someone looks at it. Events your own program defines are read.
- It writes only into `JFR_DIR`, with names it makes, and records only programs run by your own user. No shell is used. Program output is data, not instructions.
- A question that would read more than `JFR_MAX_OUTPUT_MB` is refused before it runs, with advice (a shorter recording or a rarer event type).

## Troubleshooting

| You see | Do this |
|---|---|
| The JDK tool `jfr` was not found | Install a JDK 17+, or set `JFR_JAVA_HOME`. |
| `Recording a JVM is off ... JFR_ALLOW_RECORD=1`, or `jfr_record` is missing | Turn on the plugin setting "Allow recording Java programs with JFR". |
| A program shows only its main class or jar | On purpose. The list never shows process command lines or program arguments, because they can hold tokens. |
| The program is missing from the list | It may hide from `jcmd` (started with the option that disables it). Start it without that. |
| `... has no <event> events` | The error lists the event types the recording has. Use one. |
| `JFR_DIR may not contain spaces` | Pick a recordings folder with no spaces. |
| Very few `jdk.ExecutionSample` samples | Normal for a program that mostly sleeps. |

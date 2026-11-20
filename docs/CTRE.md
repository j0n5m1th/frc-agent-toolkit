# The CTRE servers

CTRE makes the Talon FX, CANcoder, Pigeon 2 and other FRC devices, and a few free programs that go with them. Five servers help an assistant with them. Install them as in [Getting started](GETTING_STARTED.md). With the plugin they start on; switch off the ones you do not need in `/mcp`. Each has a `check` command (`frc-mcp <server> check`).

> Labels: **seen** means we saw it on a real computer, file, CANivore or Systemcore. **assumed** means it comes from a vendor document, from how CTRE's list looks, or from a guess. Anything unlabeled is plain code behavior.

| You want to... | Server |
|---|---|
| Know which CTRE programs fit your project, and install them | `ctre-tools` |
| Turn a `.hoot` log into a `.wpilog` | `ctre-owlet`, then the [log server](WPILOG.md) |
| Turn a MIDI file into a `.chrp` music file for Phoenix Orchestra | `ctre-passerine` |
| List, find or (if allowed) rename, reset or update a CANivore | `ctre-caniv` |
| See the CAN devices on the robot, their faults and live values | `ctre-diag` |
| Change a device's config, id or firmware | CTRE Tuner X (no server does this) |

Words: the **index** is CTRE's list of every tool version. A **channel** is a release line such as `2026` or `2027-alpha-7`. **Compliancy** is a whole number CTRE raises when a log or device format changes. A **hoot file** is a log from CTRE's signal logger.

More: [CANivore, the diagnostic reader and CTRE settings](CTRE_DEVICES.md).

## Version manager (`ctre-tools`)

The aim is a tool that **fits your project, not just the newest one.** The server reads your project's Phoenix vendordep (`CTRE_PROJECT_DIR`), finds its channel in the index, and picks the newest tool in that channel's compliancy range. A 2027 alpha tool is not chosen for a 2026 project. Without a project it uses CTRE's latest channel. Pre-release versions are only chosen for a pre-release channel. Phoenix Diagnostics Program versions match Phoenix library versions (**seen**: both 26.1.3). `caniv` and `passerine` have no known pairing: they get the newest in your channel, or the newest from any channel if it has none.

| Tool | What it does |
|---|---|
| `ctre_status` | Per tool: version to use, version to install, and `ok`, `update`, `missing`, `no_build` or `unverified`. Shows your project's Phoenix version and channel, and `phoenix6_newer` if your vendordep is behind. For Owlet it lists `gaps`: released levels with no Owlet. |
| `ctre_versions` | The versions CTRE lists for one tool, newest first. |
| `ctre_install` | Downloads one version, or the newest build for one compliancy, or (Owlet, `fill_gaps=true`) every missing level, at most 4 per call. Hidden until `CTRE_ALLOW_INSTALL=1`. |

Installing is careful:

- Only `https` links on `redist.ctr-electronics.com` are used. The size is capped, and the file must match **every** hash CTRE published, or it is deleted.
- After a good download the server writes `<file>.sha256` next to it. A tool only runs if it still matches. A file you placed yourself with no record is accepted once, only if it matches a published hash. Anything else shows as `unverified` and is never run.
- It never overwrites or deletes your own files, and never writes to your robot project. Updating the Phoenix vendordep is your decision (the [VS Code server](VSCODE.md) has `wpilib_vendordep`).
- The index is saved for 24 hours. With no network it uses the saved copy, then a copy bundled with this project (it goes out of date; `ctre_status` says which it used).

Tools live in `CTRE_TOOLS_DIR`. We found no sign that CTRE Tuner X keeps a shared tool folder (**seen**: its app data and program text on Windows, not its running). AdvantageScope's own Owlet copies (**seen**: `%APPDATA%\AdvantageScope\owlet\owlet-26.70.0-C21.exe`) and Tuner X's `caniv.exe` (only if its hash matches the index; **seen**: it matched `caniv` 1.1.1.0) are used read-only.

## Hoot to wpilog (`ctre-owlet`)

Owlet is CTRE's program that reads a hoot file and writes a `.wpilog`. Files are read from and written to `OWLET_DIR`.

| Tool | What it does |
|---|---|
| `owlet_list` | Hoot files, newest first, with each file's compliancy. |
| `owlet_info` | The log's compliancy (read from the file header), and the installed Owlet of exactly that level. If there is none, the error gives the `ctre_install` command. |
| `owlet_signals` | Signal names and ids (20 per page) and the biggest device groups. Names look like `Phoenix6/<bus>/<device>/<Signal>`. |
| `owlet_convert` | Writes chosen signals to a new `.wpilog` next to the hoot file. |

**Only an Owlet of exactly the log's compliancy reads it right** (**seen** on a compliancy 19 log: Owlet 21 and 20 refused it, 19 read all 2,124 signals, and 18 exited with success but listed only 36). So the server never uses a neighbouring level.

Owlet quirks the server guards against (all **seen**, Owlet 26.2.0 and 26.70.0):

- **A bad signal id does not stop it.** It prints `Invalid signal` and exports *every* signal: over 1 GB for a 54 MB log. So the server checks every id against the file first and **never runs Owlet with an unchecked `-s` list**. `all_signals=true` is the only way to export everything.
- **It overwrites its output.** The server refuses and asks for another `out` name.
- **It can say success without writing a file.** The server checks the file exists.

Convert only what the question needs, plus `RobotEnable`. The converted file opens with `wpilog_open` (**seen**: 4 converted signals opened as a 288 second log). How hoot timestamps line up with a robot-made log is **not checked yet**: do not compare the two by time without confirming it.

## MIDI to CHRP (`ctre-passerine`)

Passerine turns a MIDI file (`.mid`, `.midi`) into a `.chrp` file that Phoenix Orchestra plays: Talon FX motors playing music. Files are read from and written to `PASSERINE_DIR`.

| Tool | What it does |
|---|---|
| `passerine_list` | MIDI files (or, with `kind=chrp`, converted files), newest first. |
| `passerine_convert` | Converts one MIDI file to a new `.chrp` beside it. |

**Seen** with passerine 1.0.0: it silently overwrites an existing output, and a file that is not MIDI prints an error and exits 1. So the server names the output itself (or uses your `out` name) and refuses to overwrite, checks for the MIDI header (`MThd`) first, checks the output is not empty afterwards, and deletes a half-written file.

Loading the file is yours: CTRE's example uses `Orchestra.loadMusic("name.chrp")`. **Not checked:** whether a song sounds right (needs a motor), and whether a 1.0.0 file plays with Phoenix 2026. CTRE lists passerine 1.0.0 only under the 2027 alpha channel, and `ctre_status` shows a note.

## Examples

| Ask | What happens |
|---|---|
| "Are my CTRE tools up to date for my robot project?" | `ctre_status` |
| "Why did the turret motor trip in the newest hoot log?" | `owlet_list`, `owlet_info` (it names the Owlet level; `ctre_install tool=owlet compliancy=N` if missing), `owlet_signals`, `owlet_convert` with just the turret signals plus `RobotEnable`, then the [log server](WPILOG.md) |
| "What does the robot see on CAN, and does anything have faults?" | `diag_status`, then `diag_bus` and `diag_device` |

## Troubleshooting

| You see | It means |
|---|---|
| `No Owlet is installed` | Set `CTRE_ALLOW_INSTALL=1` and ask for `ctre_install` with `tool=owlet`. |
| `is a compliancy 21 log ... ctre_install tool=owlet compliancy=21` | No Owlet of exactly that level. Install the one named; a newer or older one will not do. |
| `no_build` | CTRE has no build of that tool for this computer. |
| `caniv cannot be used on this computer` | caniv is not available on macOS. |
| `Could not open device (error -102)` | No CANivore is connected by USB. `caniv_list` shows what is seen. |
| `Could not open USB connection (error -121)` | Another program (for example Tuner X) has it open. Try again. |
| `caniv_configure` or `caniv_flash` is missing | Hidden until `CANIV_ALLOW_CONFIG=1` / `CANIV_ALLOW_FLASH=1`. |
| `already exists next to ...` | Nothing is overwritten. Choose another `out` name. |
| `No Phoenix diagnostic server answered at ...` | The controller is off, not on this network, or `DIAG_HOST` is wrong. `frc-mcp ctre-diag check` shows what was tried. |
| `... is not a private address` | Not a robot network. Set `DIAG_ALLOW_ANY_HOST=1` only if you mean it. |
| `The diagnostic server lists no device 'x'` | Name a device as `diag_status` lists it. |
| `Specified device was not found (error -120)` | The device went away (power, wiring) after the list. |
| `No CAN device answered` | Check power, both CAN wires, termination, and the CANivore's USB. `diag_bus` shows counters of buses seen earlier. |

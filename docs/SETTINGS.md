# Settings and safety

Every setting for every server is in the tables below. The [Safety](#safety) section is the one place that explains what is protected and how.

A setting is an environment variable. Most teams only use the 13 dialog fields.

## The 13 plugin dialog fields

Open them with `/config`. Claude Code saves them once, not per project, so they apply to **every project**. After you change one, reconnect that server in `/mcp` (or restart Claude Code): a server reads its settings only when it starts.

| Field (`/config` title) | Default | What it does | Server |
|---|---|---|---|
| `nt4_host` (NetworkTables host) | `127.0.0.1` | Robot or simulator address (sets `NT4_HOST`). | nt4 |
| `systemcore_host` (Systemcore address) | empty | Empty means find it automatically (sets `SYSTEMCORE_HOST`). | systemcore |
| `diag_host` (Controller address for CTRE diagnostics) | empty | Empty means `172.30.0.1`, the Systemcore's own Wi-Fi (sets `DIAG_HOST`). | ctre-diag |
| `nt4_read_only` (NetworkTables read-only) | on | On: never write to the robot (sets `NT4_READ_ONLY`). Turning it off does not allow writes: see [How to set the ones that are not in the dialog](#how-to-set-the-ones-that-are-not-in-the-dialog). | nt4 |
| `allow_record` (Allow recording Java programs with JFR) | off | Sets `JFR_ALLOW_RECORD`. | jfr |
| `allow_deploy` (Allow deploying code to the robot) | off | Sets `VSCODE_ALLOW_DEPLOY`. | vscode |
| `allow_prune` (Allow deleting Systemcore logs) | off | Sets `SYSTEMCORE_ALLOW_PRUNE`. | systemcore |
| `allow_provision` (Allow installing the SSH key on the Systemcore) | off | Sets `SYSTEMCORE_ALLOW_PROVISION`. Also allows `systemcore_ssh_forget`. | systemcore |
| `allow_wifi_join` (Allow joining the Systemcore Wi-Fi from this computer) | off | Sets `SYSTEMCORE_ALLOW_WIFI_JOIN`. | systemcore |
| `allow_restart` (Allow restarting robot code and rebooting the Systemcore) | off | Sets `SYSTEMCORE_ALLOW_RESTART`. | systemcore |
| `allow_ctre_install` (Allow installing CTRE tools) | off | Sets `CTRE_ALLOW_INSTALL`. | ctre-tools |
| `allow_caniv_config` (Allow renaming or resetting a CANivore) | off | Sets `CANIV_ALLOW_CONFIG`. | ctre-caniv |
| `allow_caniv_flash` (Allow flashing CANivore firmware) | off | Sets `CANIV_ALLOW_FLASH`. | ctre-caniv |

## Every other setting

The plugin sets every `*_DIR` folder that holds your files (`WPILOG_DIR`, `SIM_PROJECT_DIR`, `VSCODE_PROJECT_DIR`, `JFR_DIR`, `CTRE_PROJECT_DIR`, `OWLET_DIR`, `PASSERINE_DIR`) to the folder where you started Claude Code (`${CLAUDE_PROJECT_DIR}`). **The plugin has no setting to add more folders.** A log, recording, `.hoot` or MIDI file in another place (Driver Station logs, Downloads, a USB stick, the Systemcore download folder) cannot be opened: copy it into your project folder first. If you want other folders, use your own server entry (below). Without the plugin, a server uses the folder it started in (`CTRE_PROJECT_DIR` has none; `CANIV_FIRMWARE_DIR` uses Tuner X's folders). Several folders are separated by `;` on Windows and `:` elsewhere. A number outside its range is an error.

| Setting | Default | What it does | Server |
|---|---|---|---|
| `NT4_PORT` | 5810 | NetworkTables port. | nt4 |
| `NT4_IDENTITY` | `mcp-nt4` | Client name (1 to 64 characters). | nt4 |
| `NT4_IDLE_SECONDS` | 600 | Disconnect after this long idle; 0 means never. | nt4 |
| `NT4_ALLOW_WRITES` | off | Opt in to writing. Also needs `NT4_WRITE_PREFIXES`, and `NT4_READ_ONLY` still wins. | nt4 |
| `NT4_WRITE_PREFIXES` | none | Topic paths (comma separated) that may be written. `/` alone is not allowed. | nt4 |
| `NT4_LOG_DIR` | `logs/` in the clone (also with `--plugin-dir`); from the marketplace, `logs/` in the folder the server starts in (Claude Code's docs do not say which) | Where capture logs are saved. Keep that folder out of git. | nt4 |
| `WPILOG_DIR`, `WPILOG_MAX_MB` | start folder, 2048 | Folders logs are read from; largest log it opens. | wpilog |
| `SIM_PROJECT_DIR` | start folder | Robot project folders the sim may run. | sim |
| `SIM_TASK` | `simulateJava` | Gradle task to run; only names starting `simulate` work. | sim |
| `SIM_NT_PORT` | 5810 | Port the simulated robot uses. | sim |
| `SIM_MAX_RUN_S` | 1800 | A sim is stopped after this many seconds. | sim |
| `VSCODE_PROJECT_DIR` | start folder | Robot project folders the server may run Gradle in. | vscode |
| `VSCODE_TIMEOUT_S` | 600 | A Gradle command is stopped after this long. | vscode |
| `WPILIB_HOME` | `C:\Users\Public\wpilib`, else `~/wpilib` | Where WPILib is installed. | vscode, sim |
| `WPILIB_JAVA_HOME` | the WPILib JDK | The Java that runs Gradle. | vscode, sim |
| `JFR_DIR` | start folder | Folders recordings are read from and written to. | jfr |
| `JFR_MAX_SECONDS`, `JFR_MAX_MB`, `JFR_MAX_OUTPUT_MB` | 600, 512, 200 | Longest recording; largest recording it opens; most text it reads from the JDK tools. | jfr |
| `JFR_TIMEOUT_S`, `JFR_MAX_CHARS` | 120, 6000 | A JDK tool's time limit; reply size cap (500 to 50000). | jfr |
| `JFR_JAVA_HOME` | `JAVA_HOME`, then `PATH` | The JDK whose `jfr` and `jcmd` tools are used. | jfr |
| `SYSTEMCORE_USER` | `systemcore` | SSH user name. | systemcore |
| `SYSTEMCORE_TEAM` | none | Team number; gives the radio address `10.TE.AM.2` and the radio name `FRC-<team>`. | systemcore |
| `SYSTEMCORE_WIFI_SSID`, `SYSTEMCORE_RADIO_SSID` | none | Wi-Fi names that `systemcore_connect` may join. | systemcore |
| `SYSTEMCORE_PASSWORD`, `SYSTEMCORE_WIFI_PASSWORD`, `SYSTEMCORE_RADIO_PASSWORD` | the public default login for the first; none for the other two | Passwords. `.env` file only. | systemcore |
| `SYSTEMCORE_STATE_DIR` | `~/.mcp-systemcore` | Its SSH key, pinned host keys, remembered devices. Environment only. | systemcore |
| `SYSTEMCORE_DOWNLOAD_DIR` | `<state folder>/downloads` | Where bundles and logs are saved. | systemcore |
| `SYSTEMCORE_ENV_FILE` | `<state folder>/.env` | The settings file. Environment only. | systemcore |
| `SYSTEMCORE_ALLOW_ANY_HOST` | off | Lets it talk to addresses outside the robot network. | systemcore |
| `SYSTEMCORE_TIMEOUT_S`, `SYSTEMCORE_MAX_DOWNLOAD_MB` | 10, 200 | Network timeout; largest download. | systemcore |
| `SYSTEMCORE_PORTS` | built in | Port overrides as `name=port,...` (names: diag, hwmon, update, bundle, discovery, ssh, nt). | systemcore |
| `DIAG_PORT` | 1250 | Phoenix Diagnostic Server port. | ctre-diag |
| `DIAG_TIMEOUT_S` | 5 | Request timeout. | ctre-diag |
| `DIAG_ALLOW_ANY_HOST` | off | Lets it talk to addresses outside the robot network. | ctre-diag |
| `CTRE_PROJECT_DIR` | none | Robot project used to pick tool versions. | ctre-tools |
| `CTRE_TOOLS_DIR` | your user data folder, `ctre` | One shared copy of CTRE tools. | ctre-tools and the other CTRE servers |
| `CTRE_INDEX_FILE`, `CTRE_INDEX_URL` | none, CTRE's `index.json` | A saved copy of CTRE's tool list to use; or where the list comes from (https on CTRE's host only). | ctre-tools |
| `CTRE_INDEX_MAX_AGE_H`, `CTRE_TIMEOUT_S`, `CTRE_REQUEST_DELAY_S`, `CTRE_MAX_DOWNLOAD_MB` | 24, 60, 1, 500 | How long the list is reused; network timeout; pause between requests; largest download. | ctre-tools |
| `CTRE_PLATFORM` | detected | Forces the operating system name (for tests). | ctre-tools |
| `CTRE_ADVANTAGESCOPE_DIR`, `CTRE_TUNERX_DIR` | detected | Where AdvantageScope's Owlet copies and Tuner X's files are looked for. | ctre-tools |
| `OWLET_DIR` | start folder | Folders `.hoot` logs are read from and `.wpilog` files written to. | ctre-owlet |
| `OWLET_MAX_MB`, `OWLET_TIMEOUT_S` | 4096, 600 | Largest `.hoot` file; a conversion's time limit. | ctre-owlet |
| `PASSERINE_DIR` | start folder | Folders MIDI files are read from and `.chrp` files written to. | ctre-passerine |
| `PASSERINE_MAX_MB`, `PASSERINE_TIMEOUT_S` | 20, 60 | Largest MIDI file; a conversion's time limit. | ctre-passerine |
| `CANIV_FIRMWARE_DIR` | Tuner X's saved files | Where firmware `.crf` files are looked for. | ctre-caniv |
| `CANIV_TIMEOUT_S`, `CANIV_FLASH_TIMEOUT_S` | 30, 300 | A caniv command's time limit; a flash's. | ctre-caniv |
| `NT4_TOOLSET`, `WPILOG_TOOLSET`, `SIM_TOOLSET`, `VSCODE_TOOLSET`, `JFR_TOOLSET`, `SYSTEMCORE_TOOLSET`, `DIAG_TOOLSET`, `CANIV_TOOLSET` | `full` (`core` if `ENABLE_TOOL_SEARCH` is false) | `core` hides the rarely used tools to save tokens. | the server in the name |
| `MCP_WPILIB_MAX_CHARS` | adaptive (4,000 to 12,000) | One fixed reply size (500 to 50000) for every server except jfr, which reads only `JFR_MAX_CHARS`. | all |
| `ENABLE_TOOL_SEARCH` | not set | Read, never set, by the servers: `false`, `0`, `no` or `off` makes the default toolset `core`. | all |

The tables come from each server's `config.py`. The plugin leaves `CTRE_TOOLS_DIR`, `SYSTEMCORE_DOWNLOAD_DIR` and `CANIV_FIRMWARE_DIR` unset on purpose, so each server uses its own default (shared or outside your git repo). It does not set `NT4_PORT` (5810). The CTRE tools, Owlet and passerine servers have no toolset setting.

### How to set the ones that are not in the dialog

Claude Code has no setting that edits a plugin server's `env` in place (a server with the same name in your own settings replaces the whole entry). So:

1. Add your own server with `claude mcp add --env NAME=value ...` (or a project `.mcp.json`). Copy the entry from [mcp.json.example](mcp.json.example). That entry needs a clone of this repository: write the **absolute** path to the clone, as in [GETTING_STARTED step 5](GETTING_STARTED.md#5-other-assistants). A relative path would point into your robot project.
2. In `/mcp`, switch off the plugin's server of the same kind, so there are not two.

The plugin's `env` sets only the variables in its `.mcp.json`. `NT4_ALLOW_WRITES` and `NT4_WRITE_PREFIXES` are not in it. Claude Code's docs do not say whether a server also gets variables from your shell, so do not set those two in the shell that starts Claude Code unless you want writes. The Systemcore server also reads its `SYSTEMCORE_*` settings from its `.env` file (the real environment wins), except the two marked environment only. In the plugin the dialog always sets the "Allow" switches, so an `ALLOW` line in that file does nothing, except `SYSTEMCORE_ALLOW_ANY_HOST`, which is not in the dialog. Passwords and Wi-Fi names go in that file (see [SYSTEMCORE](SYSTEMCORE.md)). In any `.env` file, a value that starts with a quote must end with one: if not, the error names the line, never the value.

## Safety

Read this before turning on any switch.

### The rules

- **Read-only by default.** A switch value that is not a yes or no word is an error, never "on".
- **Switches apply to every project.** Turn each "Allow" switch off again after you use it.
- **Most gated tools are hidden when off,** and each refuses on its own too. `systemcore_logs_prune`, `systemcore_connect` and `systemcore_disconnect` stay visible and refuse when called.
- **Device and robot text is data, not instructions.** It is shown to you, never obeyed.
- **A changed setting needs a reconnect.** Servers read settings only when they start. After you change one in `/config`, reconnect that server in `/mcp`.
- **Too-wide folders stop the list tools.** If you open Claude Code in your home folder or a drive root, the log, simulation, VS Code, profiling (jfr) and CTRE tools that search folders answer with a clear message instead of searching everything. Open Claude Code in your robot project folder. A `*_DIR` setting that names a drive root or your home folder is refused the same way.
- **`nt4_connection` and `NT4_HOST` take robot-network addresses only.** Port 1740 is refused. So is any public IP address. A name that resolves to a public address is refused too. A name that does not resolve here (like `roborio-TEAM-frc.local`) is tried anyway. Use `127.0.0.1`, a `10.TE.AM.2` address or a `roborio-TEAM-frc.local` name.
- **Errors are masked.** The assistant sees the text of our own errors, which say how to fix the problem. An error from the operating system (a locked or missing file, a timeout) shows only its kind and a short reason, never a file path. Any other error is hidden.
- **Python environment.** The plugin keeps it in a `.venv` folder inside the plugin folder, so a plugin update builds it again. It is not yet moved to the plugin's data folder (`CLAUDE_PLUGIN_DATA`). With `--plugin-dir`, the `.venv` is inside the folder you point at.

### The confirm-first hook

The plugin's `PreToolUse` hook makes Claude Code ask a person before each call of a tool that changes something: `nt4_set_value`, `nt4_release_publisher`, `wpilib_deploy`, `wpilib_vendordep`, `wpilib_set_team`, `jfr_record`, `jfr_live`, `ctre_install`, `caniv_configure`, `caniv_flash`, `systemcore_restart_robot_code`, `systemcore_reboot`, `systemcore_provision`, `systemcore_ssh_forget`, `systemcore_logs_prune` (it asks on the dry run too), `systemcore_bundle` (it makes the robot controller work for a few seconds, so never during a match), `systemcore_connect` and `systemcore_disconnect`. It runs `frc-mcp hook ask "<reason>"`, which prints one fixed JSON line (`permissionDecision: "ask"`). It is a second lock; the switches are the first. A `deny` permission rule still wins. The hook sets `onFailure: block`, so if it cannot start (for example `uv` is not on the PATH), times out or fails, Claude Code blocks the call instead of letting it through. **This needs Claude Code 2.1.295 or newer** (`claude --version`). On an older version a failed hook lets calls through. Seen in headless `claude -p` runs on Claude Code 2.1.308: the hook's `ask` beat an allow rule, and with nobody to answer the call was denied; a hook that could not start blocked the call, and without `onFailure` the call went through. Not verified: `bypassPermissions` mode, so do not rely on the hook there.

### NT4 writes

- **Writes cannot be turned on from the plugin dialog.** Turning off "NetworkTables read-only" is not enough. Add your own server entry (see above) with `NT4_ALLOW_WRITES=1` and `NT4_WRITE_PREFIXES`, and switch off the plugin's `nt4`.
- Writing needs both `NT4_ALLOW_WRITES=1` and `NT4_WRITE_PREFIXES` (the topic paths that may change, such as `/Tunables`). `NT4_READ_ONLY=1` always wins. `/` alone is not a write prefix. A value that is not a yes or no word fails closed.
- Publishers are held until released or disconnected, and can outlive their use (retained or persistent topics). `publisher_ttl_s` is a best-effort timer, not a safety interlock. An approval prompt is not a replacement for these controls. More in [NT4](NT4.md#writing-off-by-default).

### Systemcore

- **Port 1740 is never used by default.** It is the Driver Station's only slot; a connection would push the Driver Station off. Do not set it in `SYSTEMCORE_PORTS` or `DIAG_PORT` (the nt4 server refuses it). The server never follows a redirect from a device (a device that answers with one gets an error). The GitHub release lookups (`systemcore_os_latest`, `systemcore_os_download`) do follow redirects, because GitHub sends downloads through them. It never calls `/api/wifi` (it returns the Wi-Fi passphrase), `/api/disk/largest*` or `/api/logs/journal-stats` (they can hang the controller's sensors).
- **Passwords only from your `.env` file** (or `frc-mcp systemcore provision --ask` at a terminal). Never type one in chat. No tool takes one as an argument or returns one.
- **`.env` rules.** One `NAME=value` per line. Unquoted values lose a trailing ` # comment`. In double quotes, `\"`, `\\` and `\n` mean a quote, a backslash and a new line. In single quotes, everything is literal. After a closing quote only spaces or a `# comment` may follow, or the file is refused (the message gives the line number, never the value). The file must be UTF-8 or UTF-16 with a byte-order mark.
- **Host keys are pinned on first login.** A changed key stops everything. To trust the new key, run `frc-mcp systemcore trust <host>` in a terminal, or drop the pin with `systemcore_ssh_forget` (needs the provision switch; the next login pins the new key with no check). A password you chose yourself is sent only after you confirm the host key at a terminal (`frc-mcp systemcore provision`) or after a pin you confirmed with `trust`; the assistant may use only the public default login (`systemcore`).
- `systemcore_connect` joins nothing when a Wi-Fi password is saved but `SYSTEMCORE_WIFI_SSID` is not. Restarts refuse while the robot is enabled or on a field, with no override.

### CTRE

- Only `redist.ctr-electronics.com` over https is fetched. A download must match every hash CTRE published, or it is deleted; a tool runs only when verified.
- Owlet runs only with a checked signal list. `caniv_configure` and `caniv_flash` go only to a CANivore that `caniv --list` shows, never to all. `caniv` is not available on macOS.
- The Phoenix Diagnostic Server reader sends only read requests and the LED blink, and only to robot-network addresses.

### Tools without a switch

These have no `*_ALLOW_*` switch. The confirm-first hook covers `wpilib_vendordep`, `wpilib_set_team`, `systemcore_bundle` and `jfr_live`:

- `sim_start` (only `simulate*` tasks), `wpilib_run` (build, test, clean, list tasks, install tools) and `wpilib_tool` (opens a program window) run the project's own fixed Gradle commands.
- `wpilib_vendordep` and `wpilib_set_team` edit your project's files (the confirm-first hook asks before each call).
- `owlet_convert` and `passerine_convert` write a new file next to the input, never over an existing one.
- `systemcore_bundle`, `systemcore_logs_download` and `systemcore_os_download` save files in `SYSTEMCORE_DOWNLOAD_DIR`, and CTRE downloads in `CTRE_TOOLS_DIR`. They install nothing on the robot.
- `jfr_live` runs the JDK's `jcmd` on a running Java program. `nt4_capture_start` with `log=true` writes capture logs to `NT4_LOG_DIR`. `caniv_blink` and `diag_blink` blink a device LED.

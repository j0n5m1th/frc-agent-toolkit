# Settings

Every setting for every server is in the tables below. [Safety](SAFETY.md) is the one place that explains what is protected and how.

A setting is an environment variable. Most teams only use the 15 dialog fields.

Safety (what is protected and how) is in [SAFETY.md](SAFETY.md).

## The 15 plugin dialog fields

Open them with `/config`. Claude Code saves them once, not per project, so they apply to **every project**. After you change one, reconnect that server in `/mcp` (or restart Claude Code): a server reads its settings only when it starts.

| Field (`/config` title) | Default | What it does | Server |
|---|---|---|---|
| `nt4_host` (NetworkTables host) | `127.0.0.1` | Robot or simulator address (sets `NT4_HOST`). | nt4 |
| `systemcore_host` (Systemcore address) | empty | Empty means find it automatically (sets `SYSTEMCORE_HOST`). | systemcore |
| `diag_host` (Controller address for CTRE diagnostics) | empty | Empty means `172.30.0.1`, the Systemcore's own Wi-Fi (sets `DIAG_HOST`). | ctre-diag |
| `nt4_read_only` (NetworkTables read-only) | on | On: never write to the robot (sets `NT4_READ_ONLY`). Turning it off does not allow writes: see [How to set the ones that are not in the dialog](#how-to-set-the-ones-that-are-not-in-the-dialog). | nt4 |
| `read_outside_logs` (Read logs from other folders) | on | Sets `WPILOG_ALLOW_OUTSIDE`. A log outside the project folder may be opened; Claude Code asks first, every time. | wpilog |
| `allow_record` (Allow recording Java programs with JFR) | off | Sets `JFR_ALLOW_RECORD`. | jfr |
| `allow_deploy` (Allow deploying code to the robot) | off | Sets `VSCODE_ALLOW_DEPLOY`. | vscode |
| `allow_prune` (Allow deleting Systemcore logs) | off | Sets `SYSTEMCORE_ALLOW_PRUNE`. | systemcore |
| `allow_provision` (Allow installing the SSH key on the Systemcore) | off | Sets `SYSTEMCORE_ALLOW_PROVISION`. Also allows `systemcore_ssh_forget`. | systemcore |
| `allow_wifi_join` (Allow joining the Systemcore Wi-Fi from this computer) | off | Sets `SYSTEMCORE_ALLOW_WIFI_JOIN`. | systemcore |
| `allow_restart` (Allow restarting robot code and rebooting the Systemcore) | off | Sets `SYSTEMCORE_ALLOW_RESTART`. | systemcore |
| `allow_ctre_install` (Allow installing CTRE tools) | off | Sets `CTRE_ALLOW_INSTALL`. | ctre-tools |
| `allow_caniv_config` (Allow renaming or resetting a CANivore) | off | Sets `CANIV_ALLOW_CONFIG`. | ctre-caniv |
| `allow_caniv_flash` (Allow flashing CANivore firmware) | off | Sets `CANIV_ALLOW_FLASH`. | ctre-caniv |
| `learning_mode` (Learning mode) | off | Sets `FRC_LEARNING_MODE`: some replies add a short `learn` tip: why something works, or something interesting. | all |

## Every other setting

Without the plugin, set `FRC_LEARNING_MODE=1` on a server's entry to turn on learning mode (a `learn` tip on some replies; off by default).

The plugin sets every `*_DIR` folder that holds your files (`WPILOG_DIR`, `SIM_PROJECT_DIR`, `VSCODE_PROJECT_DIR`, `JFR_DIR`, `CTRE_PROJECT_DIR`, `OWLET_DIR`, `PASSERINE_DIR`) to the folder where you started Claude Code (`${CLAUDE_PROJECT_DIR}`); **it has no setting to add more.** The log server alone may read a log elsewhere (`read_outside_logs`; Claude Code asks first). A recording, `.hoot` or MIDI file elsewhere cannot be opened: copy it into your project folder, or use your own server entry (below). Without the plugin, a server uses the folder it started in (`CTRE_PROJECT_DIR` has none; `CANIV_FIRMWARE_DIR` uses Tuner X's folders). Separate several folders with `;` (Windows) or `:`. A number outside its range is an error.

| Setting | Default | What it does | Server |
|---|---|---|---|
| `NT4_PORT` | 5810 | NetworkTables port. | nt4 |
| `NT4_IDENTITY` | `mcp-nt4` | Client name (1 to 64 characters). | nt4 |
| `NT4_IDLE_SECONDS` | 600 | Disconnect after this long idle; 0 means never. | nt4 |
| `NT4_ALLOW_WRITES` | off | Opt in to writing. Also needs `NT4_WRITE_PREFIXES`, and `NT4_READ_ONLY` still wins. | nt4 |
| `NT4_WRITE_PREFIXES` | none | Topic paths (comma separated) that may be written. `/` alone is not allowed. | nt4 |
| `NT4_LOG_DIR` | `logs/` in the clone (also with `--plugin-dir`); from the marketplace, `logs/` in the folder the server starts in (Claude Code's docs do not say which) | Where capture logs are saved. Keep that folder out of git. | nt4 |
| `WPILOG_DIR`, `WPILOG_MAX_MB` | start folder, 2048 | Folders logs are read from; largest log it opens. | wpilog |
| `WPILOG_ALLOW_OUTSIDE` | off (plugin: on) | Allows a log outside them. Only the plugin asks first. | wpilog |
| `SIM_PROJECT_DIR` | start folder | Robot project folders the sim may run. | sim |
| `SIM_TASK` | `simulateJava` (`run` for 2027) | Gradle task; only `run` and `simulate*` work. | sim |
| `SIM_NT_PORT` | 5810 | Port the simulated robot uses. | sim |
| `SIM_MAX_RUN_S` | 1800 | A sim is stopped after this many seconds. | sim |
| `VSCODE_PROJECT_DIR` | start folder | Robot project folders the server may run Gradle in. | vscode |
| `VSCODE_TIMEOUT_S` | 600 | A Gradle command is stopped after this long. | vscode |
| `WPILIB_HOME` | `C:\Users\Public\wpilib`, else `~/.local/share/wpilib` on Linux if it exists, else `~/wpilib` | Where WPILib is installed. | vscode, sim |
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

The tables come from each server's `config.py`. The plugin leaves `CTRE_TOOLS_DIR`, `SYSTEMCORE_DOWNLOAD_DIR`, `CANIV_FIRMWARE_DIR` and `NT4_PORT` unset on purpose, so each server uses its own default. The CTRE tools, Owlet and passerine servers have no toolset setting.

### How to set the ones that are not in the dialog

Claude Code has no setting that edits a plugin server's `env` in place (a server with the same name in your own settings replaces the whole entry). So:

1. Add your own server with `claude mcp add --env NAME=value ...` (or a project `.mcp.json`). Copy the entry from [mcp.json.example](mcp.json.example). That entry needs a clone of this repository: write the **absolute** path to the clone, as in [GETTING_STARTED step 5](GETTING_STARTED.md#5-other-assistants). A relative path would point into your robot project.
2. In `/mcp`, switch off the plugin's server of the same kind, so there are not two.

The plugin's `env` sets only the variables in its `.mcp.json`. `NT4_ALLOW_WRITES` and `NT4_WRITE_PREFIXES` are not in it. Claude Code's docs do not say whether a server also gets variables from your shell, so do not set those two there unless you want writes. The Systemcore server also reads its `SYSTEMCORE_*` settings from its `.env` file (the real environment wins), except the two marked environment only. In the plugin the dialog always sets the "Allow" switches, so an `ALLOW` line in that file does nothing, except `SYSTEMCORE_ALLOW_ANY_HOST`, which is not in the dialog. Passwords and Wi-Fi names go there ([SYSTEMCORE](SYSTEMCORE.md)). In any `.env` file, a value starting with a quote must end with one, or the error names the line, never the value.

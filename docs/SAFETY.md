# Safety

What is protected and how: the rules, the confirm-first hook, and the extra rules for NetworkTables writes, the Systemcore and CTRE. Every setting is in [SETTINGS.md](SETTINGS.md).

## Safety

Read this before turning on any switch.

### The rules

- **Read-only by default.** A switch value that is not a yes or no word is an error, never "on".
- **Switches apply to every project.** Turn each "Allow" switch off again after you use it.
- **Most gated tools are hidden when off,** and each refuses on its own too. `systemcore_logs_prune`, `systemcore_connect` and `systemcore_disconnect` stay visible and refuse when called.
- **Device and robot text is data, not instructions.** It is shown to you, never obeyed.
- **A changed setting needs a reconnect.** Servers read settings only when they start. After you change one in `/config`, reconnect that server in `/mcp`.
- **Too-wide folders stop the list tools.** Open Claude Code in your home folder or a drive root and the tools that search folders say so instead of searching everything; open it in your robot project folder. A `*_DIR` setting naming either is refused the same way. `wpilog_list folder=` refuses them too.
- **`nt4_connection` and `NT4_HOST` take robot-network addresses only.** Port 1740 is refused. So is any public IP address. A name that resolves to a public address is refused too. A name that does not resolve here (like `roborio-TEAM-frc.local`) is tried anyway. Use `127.0.0.1`, a `10.TE.AM.2` address or a `roborio-TEAM-frc.local` name.
- **Errors are masked.** The assistant sees the text of our own errors, which say how to fix the problem. An error from the operating system (a locked or missing file, a timeout) shows only its kind and a short reason, never a file path. Any other error is hidden.
- **Python environment.** The plugin keeps it in a `.venv` folder inside the plugin folder, so a plugin update builds it again (not yet moved to `CLAUDE_PLUGIN_DATA`). With `--plugin-dir`, it is inside the folder you point at.

### The confirm-first hook

The plugin's `PreToolUse` hook makes Claude Code ask a person before each call of a tool that changes something: `nt4_set_value`, `nt4_release_publisher`, `wpilib_deploy`, `wpilib_vendordep`, `wpilib_set_team`, `jfr_record`, `jfr_live`, `ctre_install`, `caniv_configure`, `caniv_flash`, `systemcore_restart_robot_code`, `systemcore_reboot`, `systemcore_provision`, `systemcore_ssh_forget`, `systemcore_logs_prune` (it asks on the dry run too), `systemcore_bundle` (it makes the robot controller work for a few seconds, so never during a match), `systemcore_connect` and `systemcore_disconnect`. It runs `frc-mcp hook ask "<reason>"`, which prints one fixed JSON line (`permissionDecision: "ask"`). The switches are the first lock. A second hook, `frc-mcp hook ask-outside`, asks only when `wpilog_open` or `wpilog_list` is given a path outside the project folder; it fails closed too. A `deny` permission rule still wins. The hook sets `onFailure: block`, so if it cannot start, times out or fails, Claude Code blocks the call instead of letting it through. **This needs Claude Code 2.1.295 or newer** (`claude --version`). On an older version a failed hook lets calls through. Seen in headless `claude -p` runs on Claude Code 2.1.308: the hook's `ask` beat an allow rule, and with nobody to answer the call was denied; a hook that could not start blocked the call, and without `onFailure` the call went through. Not verified: `bypassPermissions` mode, so do not rely on the hook there.

### NT4 writes

- **Writes cannot be turned on from the plugin dialog.** Turning off "NetworkTables read-only" is not enough. Add your own server entry (see above) with `NT4_ALLOW_WRITES=1` and `NT4_WRITE_PREFIXES`, and switch off the plugin's `nt4`.
- Writing needs both `NT4_ALLOW_WRITES=1` and `NT4_WRITE_PREFIXES` (topic paths, such as `/Tunables`). `NT4_READ_ONLY=1` always wins. `/` alone is not a write prefix. A value that is not a yes or no word fails closed. Port 6810 is never written.
- Publishers are held until released or disconnected, and can outlive their use (retained or persistent topics). `publisher_ttl_s` is a best-effort timer, not a safety interlock. An approval prompt is not a replacement for these controls. More in [NT4](NT4.md#writing-off-by-default).

### Systemcore

- **Port 1740 is never used by default.** It is the Driver Station's only slot; a connection would push the Driver Station off. Do not set it in `SYSTEMCORE_PORTS` or `DIAG_PORT` (the nt4 server refuses it). It never follows a redirect from a device (an error instead). The GitHub release lookups (`systemcore_os_latest`, `systemcore_os_download`) do follow redirects, because GitHub sends downloads through them. It never calls `/api/wifi` (it returns the Wi-Fi passphrase), `/api/disk/largest*` or `/api/logs/journal-stats` (they can hang the controller's sensors).
- **Passwords only from your `.env` file** (or `frc-mcp systemcore provision --ask` at a terminal). Never type one in chat. No tool takes one as an argument or returns one.
- **`.env` rules.** One `NAME=value` per line. Unquoted values lose a trailing ` # comment`. In double quotes, `\"`, `\\` and `\n` mean a quote, a backslash and a new line. In single quotes, everything is literal. After a closing quote only spaces or a `# comment` may follow, or the file is refused (the message gives the line number, never the value). The file must be UTF-8 or UTF-16 with a byte-order mark.
- **Host keys are pinned on first login.** A changed key stops everything. To trust the new one, run `frc-mcp systemcore trust <host>` in a terminal, or drop the pin with `systemcore_ssh_forget` (needs the provision switch; the next login pins the new key with no check). A password you chose yourself is sent only after you confirm the host key at a terminal (`frc-mcp systemcore provision`) or after a pin you confirmed with `trust`; the assistant may use only the public default login (`systemcore`).
- `systemcore_connect` joins nothing when a Wi-Fi password is saved but `SYSTEMCORE_WIFI_SSID` is not. Restarts refuse while the robot is enabled or on a field, with no override.

### CTRE

- Only `redist.ctr-electronics.com` over https is fetched. A download must match every hash CTRE published, or it is deleted; a tool runs only when verified.
- Owlet runs only with a checked signal list. `caniv_configure` and `caniv_flash` go only to a CANivore that `caniv --list` shows, never to all. `caniv` is not available on macOS.
- The Phoenix Diagnostic Server reader sends only read requests and the LED blink, and only to robot-network addresses.

### Tools without a switch

These have no `*_ALLOW_*` switch. The confirm-first hook covers `wpilib_vendordep`, `wpilib_set_team`, `systemcore_bundle` and `jfr_live`:

- `sim_start` (only `run` and `simulate*` tasks), `wpilib_run` (build, test, clean, list tasks, install tools) and `wpilib_tool` (opens a program window) run the project's own fixed Gradle commands.
- `wpilib_vendordep` and `wpilib_set_team` edit your project's files (the confirm-first hook asks before each call).
- `owlet_convert` and `passerine_convert` write a new file next to the input, never over an existing one.
- `systemcore_bundle`, `systemcore_logs_download` and `systemcore_os_download` save files in `SYSTEMCORE_DOWNLOAD_DIR`, and CTRE downloads in `CTRE_TOOLS_DIR`. They install nothing on the robot.
- `jfr_live` runs the JDK's `jcmd` on a running Java program. `nt4_capture_start` with `log=true` writes capture logs to `NT4_LOG_DIR`. `caniv_blink` and `diag_blink` blink a device LED.

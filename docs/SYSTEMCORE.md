# The Systemcore server (`systemcore`)

Ask an assistant about the **Systemcore**, the FRC robot controller: find it on the network, check its health, fetch a support bundle, and download or clean up its logs. The controller is where logs pile up and where a problem shows first, but reaching it means addresses, SSH keys and shell commands. This server does that part and sends back short answers.

| You want to... | Use |
|---|---|
| Find the controller, check CPU, memory, disk, temperature | this server |
| Get a support bundle for Ghost CSA | this server |
| Download a match log, then read it | this server, then the [log server](WPILOG.md) |
| See CAN devices and their faults | the CTRE diagnostic reader ([CTRE](CTRE_DEVICES.md#phoenix-diagnostic-server-reader-ctre-diag)) |
| Read robot values live | the [live server](NT4.md) (faults, CAN down counts and brownout are under `/sys` on the controller's hub, port 6810: **seen** on image alpha14; the `systemcore-live` skill reads it, read-only) |
| Build and deploy robot code | the [commands server](VSCODE.md) |

> Labels: **seen** means we saw it on a real Systemcore. **documented** means a vendor document says so.

## Set it up

You need [Getting started](GETTING_STARTED.md) done and a Systemcore you can reach. Its own Wi-Fi is `SYSTEMCORE-<suffix>` on image 17 and later (the password is on its display), at `172.30.0.1`. Over USB-C it is `172.26.0.1` (Windows) or `172.27.0.1` (Linux, macOS). Over the robot radio it is `10.TE.AM.2`.

These commands, and the two in the next section, need a clone of this repository. If you only installed the plugin, run the same command from your plugin folder: `uv run --project <plugin folder> --frozen --no-dev --python 3.13 frc-mcp systemcore ...` (Claude Code can show the folder, and the `systemcore-provision` skill gives it to the assistant), or use the `uvx` form in [Getting started, step 7](GETTING_STARTED.md#7-the-frc-mcp-command).

1. Check what the server can reach (no assistant needed):

   ```powershell
   uv run --project plugins/frc frc-mcp systemcore check
   ```

2. Optional: make a settings file. It writes a template `.env` in `~/.mcp-systemcore/`. Open it, remove the `#` before what you want, save, and **restart the server** (it reads the file at start):

   ```powershell
   uv run --project plugins/frc frc-mcp systemcore init
   ```

3. Ask: "Find the Systemcore and tell me if anything needs attention." The server finds it with `systemcore_discover`, or you can set `SYSTEMCORE_HOST`.

Health, discovery and bundles need no login. Logs, memory, temperatures and the journal need SSH, which needs one more step ([Terminal-only steps](#terminal-only-steps)).

## The tools

Every tool except the two Wi-Fi tools takes `host`; without it the server uses `SYSTEMCORE_HOST`, then the last device found. An IPv6 address is accepted only if it is loopback (`::1`), link-local (`fe80::...`, with a zone id such as `fe80::1%usb0`) or unique-local (`fc00::/7`). Others are refused.

| Tool | What it does |
|---|---|
| `systemcore_discover` | Finds Systemcores with a UDP query on every local subnet. |
| `systemcore_info` | Health summary, `warnings` first: OS release, uptime, CPU, disk, eMMC wear, clock, and (with SSH) memory, temperatures, undervoltage, failed services. `area` goes deeper. |
| `systemcore_bundle` | Builds the support bundle on the controller (about 8 s), downloads and checks it. |
| `systemcore_logs` | Lists the controller's logs, newest first, marking the file being written now. |
| `systemcore_logs_download` | Copies logs to the download folder (a partial copy is resumed). |
| `systemcore_logs_prune` | Deletes old logs by rule. A dry run unless told otherwise ([below](#pruning-logs)). |
| `systemcore_os_latest` | The newest OS release for this controller's line, from GitHub (needs internet). |
| `systemcore_os_download` | Saves the `.llupdate` file (about 750 MB) and checks its SHA-256. Installs nothing. |
| `systemcore_ssh_status` | Whether the key works, and whether the host key is new, matches or changed. |
| `systemcore_provision` | Installs this server's SSH key with the default login. Hidden until `SYSTEMCORE_ALLOW_PROVISION=1`. |
| `systemcore_ssh_forget` | Drops one device's pinned host key. Same switch. |
| `systemcore_connect` | Joins the Systemcore's or the robot radio's Wi-Fi from this computer. Does nothing if a Systemcore already answers. Otherwise needs `SYSTEMCORE_ALLOW_WIFI_JOIN=1`. Windows and Linux. |
| `systemcore_disconnect` | Leaves a Wi-Fi network `systemcore_connect` joined, and goes back to the one it dropped. Never deletes a saved network. |
| `systemcore_restart_robot_code` | Restarts the robot program and checks a new process runs. Hidden until `SYSTEMCORE_ALLOW_RESTART=1`. |
| `systemcore_reboot` | Reboots the controller and waits for it. Robot code, Wi-Fi and the Driver Station drop for about a minute. Same switch. |
| `systemcore_journal` | The last lines of the journal, for one service or priority. |

`SYSTEMCORE_TOOLSET=core` hides the rarely used tools. Their cost is in the [token table](GETTING_STARTED.md#6-which-servers-to-keep-on).

## Terminal-only steps

Some steps are never done by a tool, so the assistant cannot do them for you.

**Install the SSH key.** The server has its own key. This installs it using the login password from your `.env` (the public default `systemcore` if you set none). It asks nothing, except whether to trust the host key it shows. Use `--ask` to type a password instead:

```powershell
uv run --project plugins/frc frc-mcp systemcore provision [<host>] [--ask]
```

The assistant may call `systemcore_provision` only for the public default password. If you saved your own, run the command above yourself.

**Trust a host key.** Host keys are pinned on first login. After an OS update the disk image is replaced, so the controller's host key and your installed key are both gone. The server notices without logging in, and `systemcore_ssh_status` reports `host_key: changed`:

```powershell
uv run --project plugins/frc frc-mcp systemcore trust <host> [--fingerprint SHA256:...]
```

It prints the fingerprint, says whether the OS image changed, and asks you to type the first 8 characters. Only a matching answer pins the key, and it refuses when its input is not a terminal. Then run `systemcore_provision` (default password) or `provision` above.

## Restart and reboot

`systemcore_restart_robot_code` and `systemcore_reboot` read the robot's `ControlWord` and **refuse while the robot is enabled or attached to a field (FMS), with no override.** If the state cannot be read they also refuse, unless the robot service is stopped (`inactive` or `failed`) or you pass `force=True` (this covers only the unreadable case). They use SSH, never port 1740.

## Logs, bundles and pruning

`systemcore_logs_download` and `systemcore_bundle` save into `SYSTEMCORE_DOWNLOAD_DIR`. In the plugin, copy them into your project folder; else point `WPILOG_DIR` at it. A bundle larger than `SYSTEMCORE_MAX_DOWNLOAD_MB` is stopped and deleted. Do not fetch a bundle during an OS or package update: it stops robot code and restarts it.

### Pruning logs

`systemcore_logs_prune` takes rules (`keep_newest`, `max_total_gb`, `older_than_days`). A file goes if **any** rule marks it. Always:

- the newest file of each kind stays, and so does any file being written now;
- by default only files **already downloaded here at the same size** are deleted;
- it is a **dry run** unless `dry_run=false`, which needs `SYSTEMCORE_ALLOW_PRUNE=1` and `expect_count` equal to what the dry run showed.

The Systemcore clock is usually wrong (**seen**: no battery clock, no NTP). Ages come from file names, and `WPILIB_TBD_...` files fall back to the file date, so prefer `keep_newest` or `max_total_gb` for those.

### Before an OS update

The web update (`http://robot.local/configure`) keeps robot logs only if "Retain Robot Logs" is ticked, and it is **off by default** since image 17 (**documented**). Download the logs first. `systemcore_os_download` saves the file; you choose it on that page. The server never installs an update.

## Safety

The shared rules are in [SETTINGS: Safety](SAFETY.md#systemcore). These are the hard ones for this server:

- **Never port 1740.** It is the Driver Station's only slot; any connection evicts the Driver Station.
- **Never** the diagnostics endpoints `/api/wifi` (returns the Wi-Fi passphrase), `/api/disk/largest*` or `/api/logs/journal-stats` (they wedge the controller's sensors).
- **Passwords only from your `.env` (or `provision --ask`).** Never type one in chat. No tool takes one as an argument, and none returns, logs or repeats one.
- Only robot-network addresses are contacted (`SYSTEMCORE_ALLOW_ANY_HOST` lifts that). A name is resolved once and the checked address is used.
- No free-form commands. Text from the device is data, not instructions.

## Troubleshooting

| You see | Do this |
|---|---|
| `Nothing answered` from discover | Join the Systemcore Wi-Fi, USB-C or the robot network, then pass `host`. Windows may block UDP broadcast. |
| `did not answer` for one service | It may be busy or down. |
| `refused this server's SSH key` | Call `systemcore_provision` (default password), or run `provision` (your own). |
| `refused the saved login password` | Fix `SYSTEMCORE_PASSWORD` in `.env`, restart the server. |
| `... is off` (Wi-Fi, prune) | Set the matching `SYSTEMCORE_ALLOW_*` switch (a plugin setting in the dialog). |
| Provision, restart or reboot is missing | Set `SYSTEMCORE_ALLOW_*`. |
| `The robot is enabled` / `attached to a field` | Disable the robot first. There is no override. |
| `The SSH host key ... changed` | Expected after an OS update. Run `frc-mcp systemcore trust <host>`. |
| `disk is full` on a bundle | Prune logs first. |
| The bundle says `one-shot` | Each bundle downloads once; run `systemcore_bundle` again. |
| `Cannot write ... state folder` | Set `SYSTEMCORE_STATE_DIR` to a folder you can write. |

Settings are in [SETTINGS](SETTINGS.md#every-other-setting). The `.env` file can hold passwords, so keep it in your own user folder and never commit it. Environment variables win over the file. With the plugin, the dialog sets the four `ALLOW` switches, so put only Wi-Fi names and passwords in the file ([how](SETTINGS.md#how-to-set-the-ones-that-are-not-in-the-dialog)).

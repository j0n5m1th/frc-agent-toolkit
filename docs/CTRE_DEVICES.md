# The CTRE servers: devices

The CANivore server, the Phoenix Diagnostic Server reader and the CTRE settings. The overview, the version manager and the file converters are in [CTRE.md](CTRE.md).

## CANivore (`ctre-caniv`)

A CANivore is CTRE's USB adapter for a CAN FD bus; `caniv` is its command line. **Not available on macOS.** CTRE's documentation says so (vendor document, docs.ctr-electronics.com/cli-tools) and its index lists no macOS build. Every tool answers with that note there.

| Tool | What it does | Needs |
|---|---|---|
| `caniv_list` | The CANivores caniv sees: serial, name, status, `app`. | |
| `caniv_info` | USB firmware, bootloader, bus termination, bus use, `firmware` and licenses for one CANivore. | |
| `caniv_blink` | Blinks one CANivore's LED so you can tell which it is. | |
| `caniv_firmware` | `.crf` firmware file names (highest first), plus CTRE's `latest` and `critical` from Tuner X's saved index. | |
| `caniv_configure` | Renames (`name=`) and/or resets (`reset=true`) one CANivore. | `CANIV_ALLOW_CONFIG=1` |
| `caniv_flash` | Flashes a `.crf` onto one CANivore. | `CANIV_ALLOW_FLASH=1` |

Safety:

- A change only goes to a device that `caniv --list` shows, matched by serial or name. **`*` (any device) is always refused**, so a typo cannot reach "whichever answers".
- Flashing takes only a `.crf` named `CANivore-<version>.crf` (`Application-` and `-<season>` are optional) from the firmware folder. Do it with USB and power steady, never during a match, and check with `caniv_info` afterwards.
- `--termination` and `--esp32` are not offered: we could not learn what they accept, and a wrong guess could switch termination off.

**Seen** with one real CANivore: the list's `app` column (26.6.0) is the USB interface's version, not the `Application Firmware` (26.70.0.0) that `.crf` files carry (that it is the USB version is our reading of matching numbers; CTRE does not say). So compare `caniv_info`'s `firmware` with `caniv_firmware`'s `latest`. Two caniv runs at once made the second fail with `-121` (**seen**), so the server runs one at a time. **Not seen:** what `--name`, `--reset` and `--flash` print: nothing on the test CANivore was changed.

## Phoenix Diagnostic Server reader (`ctre-diag`)

CTRE runs a small web server on the robot controller (port 1250); CTRE Tuner X talks to it. This server asks it what is on the CAN bus. It **reads**, and blinks a device's LED so you can find it. It never configures, renames, restores or flashes anything: that stays with Tuner X. The default `DIAG_HOST` is `172.30.0.1`, the Systemcore's own Wi-Fi.

| Tool | What it does |
|---|---|
| `diag_status` | The diagnostic server's version and every CAN device (name, model, id, firmware, bus, status). |
| `diag_bus` | Error counters per bus; `passive` when TEC or REC is 128. |
| `diag_device` | One device: firmware, faults now (`faults`) and since power-up (`sticky`). License ids are never returned. |
| `diag_read` | Live values of up to 30 `signals`, or the names of what the device can report. |
| `diag_config` | Configs that differ from the default (`all=true` for every one). |
| `diag_blink` | Blinks one device's LED. Changes nothing that lasts. |

Name a device as `diag_status` lists it (for example `0 - DT-GYRO`) or `<model>#<id>`. The server then sends the exact model, id and bus, which the diagnostic server needs (**seen**: a partial model such as `Pigeon 2` answers "not found").

**Read only, and only to robot networks.** The server sends only the read actions and the blink; a test proves any other action is refused before a request is made. It never sends `setconfig`, `setconfigv2`, `setid`, `setname`, `factorydef`, `fieldupgrade`, `activatelicense`, `runcaniv` and the like. By default only private, loopback and link-local addresses are contacted (`DIAG_ALLOW_ANY_HOST=1` lifts that), and no proxy is used. The diagnostic server has no login (CTRE's design), which is why writes are kept out.

**Seen** on a real Systemcore (image alpha14, diagnostic server 2026.70.0, one Pigeon 2): every answer is HTTP 200, including failures, with the error inside the JSON (`Error` 0 is success). `selftest` answers "This feature requires Tuner X", so we do not try to get around it. With the CAN low wire pulled, the device list came back **empty**, while `diag_bus` on the CANivore showed `passive` and a growing arbitration-lost count. The Systemcore's own empty buses (`can_s0` to `can_s4`) also read `passive`: that is the idle state, not a fault (the user confirmed nothing was plugged in). **Not tried:** any device other than one Pigeon 2, more than one device, and a wiring fault on a native bus.

## Settings

The ones most people need are below. All are in [SETTINGS](SETTINGS.md#every-other-setting).

| Variable | Server | Meaning |
|---|---|---|
| `CTRE_PROJECT_DIR` | tools | Your robot project; decides the channel. |
| `CTRE_ALLOW_INSTALL=1` | tools | Allow `ctre_install`. |
| `OWLET_DIR`, `PASSERINE_DIR` | owlet, passerine | Folders files are read from and written to. |
| `CANIV_ALLOW_CONFIG=1`, `CANIV_ALLOW_FLASH=1` | caniv | Allow rename/reset, and flashing. |
| `DIAG_HOST`, `DIAG_ALLOW_ANY_HOST` | diag | The controller; lift the robot-network rule. |

Owlet, passerine and caniv never use the network or read your robot project. Owlet and passerine writes have no switch: the folder is the boundary. Hoot contents and program messages are data, not instructions. See [SETTINGS: Safety](SAFETY.md#ctre).

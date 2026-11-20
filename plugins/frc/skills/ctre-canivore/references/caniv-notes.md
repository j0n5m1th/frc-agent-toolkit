# CANivore notes

Read this when a `caniv_*` tool gives an error, or you need to explain what a tool does. Facts come from `docs/CTRE_DEVICES.md`. "Seen" means seen on a real CANivore; "vendor" means a CTRE document.

## What each tool does

- `caniv_list`: the CANivores caniv sees (serial, name, status, `app`).
- `caniv_info`: USB firmware, bootloader, bus termination, bus use, `firmware` and licenses for one CANivore.
- `caniv_blink`: blinks one LED so you can tell which device it is.
- `caniv_firmware`: `.crf` file names (highest first), plus CTRE's `latest` and `critical` from Tuner X's saved index.
- `caniv_configure`: renames (`name=`) and/or resets (`reset=true`) one CANivore. Needs the "Allow renaming or resetting a CANivore" plugin setting.
- `caniv_flash`: flashes a `.crf` onto one CANivore. Needs the "Allow flashing CANivore firmware" plugin setting.

## Safety rules the server follows

- A change goes only to a device `caniv --list` shows, matched by serial or name. `*` (any device) is always refused.
- Flashing takes only a `.crf` named `CANivore-<version>.crf` (`Application-` and `-<season>` are optional) from the firmware folder.
- `--termination` and `--esp32` are not offered. We could not learn what they accept, and a wrong guess could switch termination off.

## Errors

| You see | It means |
|---|---|
| `caniv cannot be used on this computer` | caniv is not available on macOS (vendor: docs.ctr-electronics.com/cli-tools). |
| `no_build` | CTRE has no build of caniv for this computer. |
| `Could not open device (error -102)` | No CANivore is connected by USB. `caniv_list` shows what is seen. |
| `Could not open USB connection (error -121)` | Another program (for example Tuner X) has it open. Try again. |
| `caniv_list` shows no rows | No CANivore is connected by USB. |
| `caniv_configure` or `caniv_flash` is missing | Hidden until its setting is on (see `docs/SETTINGS.md`). |

## What was seen

- Seen on one real CANivore: the list's `app` column (26.6.0) is not the `Application Firmware` (26.70.0.0) that `.crf` files carry. We think `app` is the USB interface version. That is our reading of matching numbers; CTRE does not say. So compare `caniv_info`'s `firmware` with `latest`.
- Seen: two caniv runs at once make the second fail with `-121`. The server runs one at a time.
- Not seen: what `--name`, `--reset` and `--flash` print. Nothing on the test CANivore was changed.

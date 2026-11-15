# Diagnose notes

Read this when a `diag_*` tool gives an error, or you need to explain a reading. Facts come from `docs/CTRE.md`. "Seen" means seen on a real Systemcore (image alpha14, diagnostic server 2026.70.0, one Pigeon 2); "not tried" means nobody has tried it.

## What each tool does

- `diag_status`: the diagnostic server's version and every CAN device (name, model, id, firmware, bus, status).
- `diag_bus`: error counters per bus; `passive` when TEC or REC is 128.
- `diag_device`: one device's firmware, faults now (`faults`) and since power-up (`sticky`). License ids are never returned.
- `diag_read`: live values of up to 30 `signals`, or the names of what the device can report.
- `diag_config`: configs that differ from the default (`all=true` for every one).
- `diag_blink`: blinks one device's LED. Changes nothing that lasts.
- `ctre_status`: per tool, the version to use, the version to install, and `ok`, `update`, `missing`, `no_build` or `unverified`.

Name a device as `diag_status` lists it (for example `0 - DT-GYRO`) or `<model>#<id>`. Seen: a partial model such as `Pigeon 2` answers "not found".

## Why it is read-only

The server sends only the read actions and the LED blink to port 1250 on the controller. The diagnostic server has no login (CTRE's design), so writes stay with Tuner X. By default only private, loopback and link-local addresses are contacted, and no proxy is used.

## What was seen

- Every answer is HTTP 200, even a failure. The error is inside the JSON (`Error` 0 is success).
- `selftest` answers "This feature requires Tuner X". Do not try to get around it.
- With the CAN low wire pulled, the device list came back empty, while `diag_bus` on the CANivore showed `passive` and a growing arbitration-lost count.
- The Systemcore's own empty buses (`can_s0` to `can_s4`) also read `passive`. That is the idle state, not a fault.
- Not tried: any device other than one Pigeon 2, more than one device, and a wiring fault on a native bus.

## Errors

| You see | It means |
|---|---|
| `No Phoenix diagnostic server answered at ...` | The controller is off, not on this network, or `host` is wrong. |
| `... is not a private address` | Not a robot network. Do not lift the rule unless the user asks. |
| `The diagnostic server lists no device 'x'` | Name a device as `diag_status` lists it. |
| `Specified device was not found (error -120)` | The device went away (power, wiring) after the list. |
| `No CAN device answered` | Check power, both CAN wires, termination, and the CANivore's USB. `diag_bus` shows counters of buses seen earlier. |
| `no_build` | CTRE has no build of that tool for this computer. |

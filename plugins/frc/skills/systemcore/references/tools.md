# Systemcore tools: options and hints

Read this when a call needs an option, or a reply is unclear. The safety rules stay in SKILL.md.

## Health
- `systemcore_info` with `area`: `summary` (default), `network`, `hardware`, `services` or `dmesg`. The summary lists `warnings` first: OS, uptime, CPU, disk, eMMC, clock. With SSH it adds memory, temperatures and failed services.
- If `errors` has an `ssh` entry, shell readings are missing. `systemcore_ssh_status` says why: the host key (new, matches, changed) and whether the login works.
- `systemcore_journal`: `lines` (1 to 300, default 60), `service` (a systemd unit such as `robot`), `priority` (`err`, `warning`, `notice` or `info`: this level and worse), `boot` (0 is this boot, -1 the one before).
- `systemcore_os_latest` says whether the controller is behind its newest OS. `refresh=true` asks GitHub now, otherwise a list saved within the hour is used.

## Logs
- `systemcore_logs`: `kind` is `wpilog`, `hoot` or `other`; `limit` up to 200. Files with `no_time_in_name` have no date, because the Systemcore clock is often wrong.
- `systemcore_logs_download`: give `paths` (from `systemcore_logs`), or `newest` (1 to 50) with `kind`. A partial copy is resumed.
- `systemcore_bundle`: `symptoms` (one sentence), `wait_s`. It takes about 8 seconds. Ghost CSA accepts the zip as is. It is one-shot: run it again for a fresh one. `include_ds_log` asks for Driver Station logs (unverified).

## Pruning (only on request)
`systemcore_logs_prune` is off until the "Allow deleting Systemcore logs" plugin setting is on, and only the dry run works without it.
- Rules (any one marks a file): `keep_newest` (per kind), `max_total_gb` (per kind), `older_than_days`. Give at least one.
- The newest file of each kind and any file being written always stay.
- `only_downloaded=true` (default) touches only files already downloaded here. The reply says how many were kept because they were not downloaded.
- Dry run first. After the user agrees, repeat with `dry_run=false` and `expect_count` set to the count the dry run showed. If the rules now match a different count, nothing is deleted: run the dry run again.

## OS file
`systemcore_os_download` saves the `.llupdate` file (about 750 MB) and checks its SHA-256. It installs nothing. The user updates at http://robot.local/configure. `tag` picks a release; `usb_boot` is rare.

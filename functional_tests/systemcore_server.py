"""Functional test of `mcp-systemcore`: the installed command, over stdio, against a fake Systemcore.

`systemcore_fake.py` serves the controller's HTTP services, the UDP discovery answer and a real SSH + SFTP server on local
ports; SYSTEMCORE_PORTS points the server at them. So this covers the whole path an assistant uses: finding the device,
the health summary with and without the SSH key, installing the key with systemcore_provision (the password comes from a `.env` file in the
state folder, as for a user; a wrong one is refused without being repeated), host-key pinning and an OS-update key change, listing, downloading and pruning logs, the
support bundle, the OS release comparison, and the refusals. The pieces that need the real internet (the GitHub release
list and the update file download) stay in the pytest suite, which fakes GitHub on a local port.
"""

import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import systemcore_fake as fake_module
from harness import (
    OUTPUT,
    Report,
    call,
    client,
    is_unknown_tool,
    server_command,
    tool_names,
)
from systemcore_fake import FakeSystemcore

FULL_TOOLS = {"systemcore_discover", "systemcore_info", "systemcore_bundle", "systemcore_logs", "systemcore_logs_download",
              "systemcore_logs_prune", "systemcore_journal", "systemcore_ssh_status", "systemcore_ssh_forget",
              "systemcore_os_latest", "systemcore_os_download", "systemcore_provision", "systemcore_connect", "systemcore_disconnect",
              "systemcore_restart_robot_code", "systemcore_reboot"}
CORE_TOOLS = {"systemcore_discover", "systemcore_info", "systemcore_bundle", "systemcore_logs", "systemcore_logs_download",
              "systemcore_logs_prune", "systemcore_ssh_status", "systemcore_os_latest", "systemcore_provision", "systemcore_connect", "systemcore_disconnect"}
GATED_TOOLS = {"systemcore_provision", "systemcore_ssh_forget", "systemcore_restart_robot_code", "systemcore_reboot"}  # hidden until their SYSTEMCORE_ALLOW_* switch is on
HOST = "127.0.0.1"


async def run(keep: bool = False, target: str = "auto", host: str | None = None) -> bool:
    """The fake Systemcore by default; a real one (read-only checks) when `target` is real, or auto and one answers."""
    real = None
    if target in ("auto", "real"):
        real = await find_real(host)
        if real is None and target == "real":
            report = Report("systemcore server (real)")
            report.check("a real Systemcore answers", False, "none found: join its Wi-Fi, or pass --host")
            return report.summary()
    if real is not None:
        return await run_real(keep, real)
    if target == "auto":
        print("\n(no real Systemcore answered; using the fake one. Use --target real to require one.)")
    return await run_fake(keep)


async def find_real(host: str | None) -> str | None:
    """The address of the real Systemcore: `host` if it answers like one, else the only device UDP discovery finds."""
    from frc_mcp_systemcore import discovery, net
    from frc_mcp_systemcore.errors import SystemcoreError

    if host:
        try:
            await net.request_json(host, "update", "/api/oshash", timeout=3)
        except SystemcoreError:
            return None
        return host
    found = await discovery.discover(2)
    return str(found[0]["host"]) if len(found) == 1 else None


def trust_at_terminal(state_folder: Path, host: str, fingerprint: str) -> None:
    """What `frc-mcp systemcore trust` stores after the person types the confirmation (a terminal cannot be driven here; the command itself is covered in pytest)."""
    from frc_mcp_systemcore import state

    previous = os.environ.get("SYSTEMCORE_STATE_DIR")
    os.environ["SYSTEMCORE_STATE_DIR"] = str(state_folder)
    try:
        state.pin(host, fingerprint, None, confirmed=True)
    finally:
        if previous is None:
            os.environ.pop("SYSTEMCORE_STATE_DIR")
        else:
            os.environ["SYSTEMCORE_STATE_DIR"] = previous


async def run_fake(keep: bool) -> bool:
    report = Report("systemcore server (fake controller)")
    folder = OUTPUT / "systemcore"
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "state").mkdir(parents=True)
    env_file = folder / "state" / ".env"
    device = FakeSystemcore()
    env = {"SYSTEMCORE_PORTS": device.start(), "SYSTEMCORE_STATE_DIR": str(folder / "state"), "SYSTEMCORE_DOWNLOAD_DIR": str(folder / "downloads"),
           "SYSTEMCORE_HOST": HOST, "SYSTEMCORE_TIMEOUT_S": "5"}
    try:
        made = subprocess.run([server_command("mcp-systemcore"), "init"], env={**os.environ, **env}, capture_output=True, text=True, timeout=60, check=False)
        report.check("`init` writes the settings file template", made.returncode == 0 and env_file.is_file() and "Created" in made.stdout, made.stdout + made.stderr)
        again = subprocess.run([server_command("mcp-systemcore"), "init"], env={**os.environ, **env}, capture_output=True, text=True, timeout=60, check=False)
        report.check("`init` never overwrites it", "Already exists" in again.stdout, again.stdout)

        async with client("mcp-systemcore", env) as c:
            report.check(f"with every switch off, the {len(GATED_TOOLS)} tools behind SYSTEMCORE_ALLOW_PROVISION / _RESTART are not listed", await tool_names(c) == FULL_TOOLS - GATED_TOOLS)
            for tool in sorted(GATED_TOOLS):
                hidden = await call(c, tool)
                report.check(f"{tool} is an unknown tool until the owner allows it", is_unknown_tool(hidden, tool), hidden)
            report.check("nothing reached the controller from those calls", not device.authorized and device.robot_pid == 100 and device.reboots == 0)
        async with client("mcp-systemcore", {**env, "SYSTEMCORE_ALLOW_PROVISION": "1"}) as c:
            report.check("SYSTEMCORE_ALLOW_PROVISION=1 lists provision and forget, not restart or reboot", await tool_names(c) == FULL_TOOLS - {"systemcore_restart_robot_code", "systemcore_reboot"})
        async with client("mcp-systemcore", {**env, "SYSTEMCORE_ALLOW_RESTART": "1"}) as c:
            report.check("SYSTEMCORE_ALLOW_RESTART=1 lists restart and reboot, not provision or forget", await tool_names(c) == FULL_TOOLS - {"systemcore_provision", "systemcore_ssh_forget"})

        wrong = "wrong-password-123"
        with env_file.open("a", encoding="utf-8") as handle:
            handle.write(f"\nSYSTEMCORE_ALLOW_PROVISION=1\nSYSTEMCORE_PASSWORD={wrong}\n")
        async with client("mcp-systemcore", env) as c:
            custom = await call(c, "systemcore_provision")
            report.check("a custom password is never used from the assistant; the user is sent to a terminal", "EXC" in custom
                         and "frc-mcp systemcore provision" in str(custom) and wrong not in str(custom) and not device.authorized and device.password_logins == 0, custom)
        env_file.write_text("SYSTEMCORE_ALLOW_PROVISION=1\nSYSTEMCORE_ALLOW_RESTART=1\n", encoding="utf-8")  # the user fixes the file (back to the default password) and restarts

        async with client("mcp-systemcore", env) as c:
            report.check(f"with the switches on in the settings file, offers exactly the {len(FULL_TOOLS)} documented tools", await tool_names(c) == FULL_TOOLS)
            report.check("offers the one prompt", {p.name for p in await c.list_prompts()} == {"systemcore_health"})

            found = await call(c, "systemcore_discover", {"host": HOST, "timeout_s": 1})
            report.check("finds the controller by the UDP query", [d.get("host") for d in found.get("devices", [])] == [HOST]
                         and found["devices"][0].get("hostname") == "robot", found)

            refused = await call(c, "systemcore_info", {"host": "8.8.8.8"})
            report.check("a public address is refused", "EXC" in refused and "not a private address" in str(refused), refused)

            # -- before the SSH key is installed: the HTTP readings work and the missing part is explained --
            status = await call(c, "systemcore_ssh_status")
            report.check("the host key was pinned at first contact; the login needs provisioning",
                         status.get("host_key") == "matches" and status.get("login") == "needs_provisioning"
                         and "systemcore_provision" in status.get("hint", ""), status)

            info = await call(c, "systemcore_info")
            report.check("info reads the HTTP services without a login", info.get("team") == 9999 and info.get("disk", {}).get("used_pct") == 90.0
                         and info.get("os", {}).get("release") == "limelightosr-2027.0.0-alpha14" and info["os"].get("hash") == "0a1b2c3d", info)
            report.check("info says which source is missing and how to fix it", "ssh" in info.get("errors", {}) and "provision" in info["errors"]["ssh"], info)
            report.check("info warns about a full disk and a stale clock", any("90.0% full" in w for w in info.get("warnings", []))
                         and any("clock" in w for w in info.get("warnings", [])), info.get("warnings"))
            report.check("there is no newer-OS claim before any release list is saved", not any("newer OS" in w for w in info.get("warnings", [])), info.get("warnings"))

            listing = await call(c, "systemcore_logs")
            report.check("log tools say the key is missing instead of failing blindly", "EXC" in listing and "provision" in str(listing), listing)

            joined = await call(c, "systemcore_connect")
            report.check("connect changes nothing when a Systemcore already answers (Wi-Fi, Ethernet or USB)", joined.get("already_reachable") is True
                         and [d.get("host") for d in joined.get("devices", [])] == [HOST], joined)
            left_wifi = await call(c, "systemcore_disconnect")
            report.check("leaving Wi-Fi is refused until the owner allows it, naming the setting", "EXC" in left_wifi and "SYSTEMCORE_ALLOW_WIFI_JOIN=1" in str(left_wifi), left_wifi)

            # -- install the key with the password saved in the .env file (the model never sees or passes it) --
            provisioned = await call(c, "systemcore_provision")
            report.check("provision installs the managed key and pins the host key", provisioned.get("login") == "ok" and device.password_logins == 1
                         and len(device.authorized) == 1 and str(provisioned.get("host_key", "")).startswith("SHA256:"), (provisioned, device.password_logins))
            report.check("no password is in the reply", wrong not in json.dumps(provisioned), provisioned)
            again = await call(c, "systemcore_provision")
            report.check("provisioning again sends no password", again.get("already_installed") is True and device.password_logins == 1, again)

            status = await call(c, "systemcore_ssh_status")
            report.check("now the host key matches and the login works", status.get("host_key") == "matches" and status.get("login") == "ok", status)

            info = await call(c, "systemcore_info")
            report.check("info adds memory, temperatures and the robot service over SSH", info.get("memory_mb") == {"total": 3906, "available": 2929}
                         and info.get("temps", {}).get("cpu_c") == 83.0 and info.get("robot_service", {}).get("restarts") == 2 and "ssh" not in info.get("errors", {}), info)
            report.check("info warns about heat, undervoltage and a failed service", all(any(part in w for w in info.get("warnings", []))
                         for part in ("CPU is 83 C", "Undervoltage", "foo.service")), info.get("warnings"))

            journal = await call(c, "systemcore_journal", {"service": "robot"})
            report.check("the journal comes back as lines", journal.get("lines") and "robot program started" in journal["lines"][0], journal)
            bad_service = await call(c, "systemcore_journal", {"service": "robot; reboot"})
            report.check("a service name that is not a unit name is refused", "EXC" in bad_service, bad_service)
            report.check("no free-form command reached the device", not any("reboot" in command for command in device.commands), device.commands)

            # -- logs --
            listing = await call(c, "systemcore_logs")
            paths = [f["path"] for f in listing.get("files", [])]
            report.check("lists every log, newest first", listing.get("total") == 5 and paths[0] == fake_module.NEWEST_WPILOG
                         and paths[-1] == fake_module.OLDER_WPILOG, listing)
            report.check("marks the file the robot is writing", [f for f in listing.get("files", []) if f.get("active")] == [
                {"path": fake_module.NEWEST_WPILOG, "mb": 0.0, "kind": "wpilog", "active": True}], listing)

            dry = await call(c, "systemcore_logs_prune", {"keep_newest": 1, "kind": "hoot"})
            report.check("prune keeps what is not downloaded yet (dry run, nothing chosen)", dry.get("dry_run") is True and dry.get("count") == 0, dry)

            saved = await call(c, "systemcore_logs_download", {"newest": 3, "kind": "hoot"})
            files = [Path(f["saved"]) for f in saved.get("files", [])]
            report.check("downloads the newest three .hoot files", len(files) == 3 and all(p.is_file() and p.stat().st_size == 3000 for p in files), saved)
            wpilog = await call(c, "systemcore_logs_download", {"paths": [fake_module.NEWEST_WPILOG, "../../etc/passwd"]})
            rows = wpilog.get("files", [])
            report.check("a path outside the log listing is refused, the real one is copied",
                         rows[0].get("saved") and Path(rows[0]["saved"]).read_bytes() == fake_module.FILES[fake_module.NEWEST_WPILOG]
                         and "not in the Systemcore log folder" in rows[1].get("err", ""), wpilog)

            dry = await call(c, "systemcore_logs_prune", {"keep_newest": 1, "kind": "hoot"})
            report.check("the dry run now lists the two older .hoot files and deletes nothing",
                         dry.get("count") == 2 and dry.get("dry_run") is True and len(device.files) == 5, dry)
            wpilog_dry = await call(c, "systemcore_logs_prune", {"keep_newest": 1, "kind": "wpilog", "only_downloaded": False})
            report.check("the newest and the active file are never chosen", wpilog_dry.get("count") == 1
                         and [f["path"] for f in wpilog_dry["files"]] == [fake_module.OLDER_WPILOG], wpilog_dry)

            refused = await call(c, "systemcore_logs_prune", {"keep_newest": 1, "kind": "hoot", "dry_run": False, "expect_count": 2})
            report.check("deleting is refused until SYSTEMCORE_ALLOW_PRUNE=1, naming it", "EXC" in refused and "SYSTEMCORE_ALLOW_PRUNE=1" in str(refused)
                         and len(device.files) == 5, refused)

            # -- the OS release comparison, from a saved release list (the GitHub fetch itself is covered by pytest) --
            (folder / "state" / "releases.json").write_text(json.dumps({"fetched_at": 1, "releases": fake_module.RELEASES}), encoding="utf-8")
            latest = await call(c, "systemcore_os_latest", {"refresh": False})
            report.check("os_latest compares the controller with the saved list", latest.get("behind") is True
                         and latest.get("newest", {}).get("tag") == "limelightosr-2027.0.0-alpha18-422" and latest.get("device") == "limelightosr-2027.0.0-alpha14", latest)
            info = await call(c, "systemcore_info")
            report.check("info now warns that a newer OS exists", any("alpha18" in w for w in info.get("warnings", [])), info.get("warnings"))
            unknown = await call(c, "systemcore_os_download", {"tag": "limelightosr-2027.0.0-alpha99"})
            report.check("downloading a tag that is not in the list is refused", "EXC" in unknown and "not in the saved release list" in str(unknown), unknown)

            # -- the support bundle --
            bundle = await call(c, "systemcore_bundle", {"symptoms": "functional test"})
            report.check("the support bundle is generated, downloaded and checked", Path(bundle.get("path", "")).is_file()
                         and zipfile.is_zipfile(bundle["path"]) and bundle.get("member_count") == 2, bundle)
            report.check("the bundle request carried the symptoms and no Driver Station log", any(
                m == "POST" and p == "/api/support/generate" and b == {"symptoms": "functional test", "lastOccurred": "", "includeDsLog": False}
                for m, p, b in device.http.requests), device.http.requests[-4:])

            # -- an OS update changes the host key --
            device.new_host_key()
            status = await call(c, "systemcore_ssh_status")
            report.check("a changed host key stops everything and gives the new fingerprint", status.get("host_key") == "changed"
                         and status.get("login") == "not tried" and status.get("presented_host_key", "").startswith("SHA256:"), status)
            blocked = await call(c, "systemcore_logs")
            report.check("log tools refuse to log in to a changed host", "EXC" in blocked and "changed" in str(blocked), blocked)
            report.check("the changed host key's message sends the user to the terminal command, not to a tool", "frc-mcp systemcore trust" in str(blocked)
                         and "systemcore_ssh_trust" not in str(blocked), blocked)
            trust_at_terminal(folder / "state", HOST, status.get("presented_host_key", ""))  # the user typed the confirmation in `frc-mcp systemcore trust`
            again = await call(c, "systemcore_ssh_status")
            report.check("the login works again with the same installed key", again.get("host_key") == "matches" and again.get("login") == "ok", again)
            forgotten = await call(c, "systemcore_ssh_forget")
            after = await call(c, "systemcore_ssh_status")
            report.check("forget drops the pin; the next sight is unpinned again", forgotten.get("forgotten") is True
                         and str(after.get("host_key")).startswith("unpinned"), (forgotten, after))

            # -- a real OS update: the same unit and address, but a new host key, a new OS hash and the installed key gone --
            before = await call(c, "systemcore_ssh_status")  # logs in once: pins the host key with the old OS hash
            report.check("before the update: pinned and logged in", before.get("host_key") == "matches" and before.get("login") == "ok", before)
            passwords_before = device.password_logins
            device.new_host_key()
            device.authorized.clear()
            device.http.routes[("GET", "/api/oshash")] = {"hash": "9999aaaa", "partition": "B", "release": "limelightosr-2027.0.0-alpha18"}
            updated = await call(c, "systemcore_ssh_status")
            report.check("after the update: the host key changed and the OS change explains it", updated.get("host_key") == "changed"
                         and "OS image changed" in updated.get("hint", "") and "frc-mcp systemcore trust" in updated.get("hint", ""), updated)
            early = await call(c, "systemcore_provision")
            report.check("provision refuses to send a password to the changed host", "EXC" in early and "host key" in str(early)
                         and device.password_logins == passwords_before and not device.authorized, early)
            trust_at_terminal(folder / "state", HOST, updated.get("presented_host_key", ""))
            lost = await call(c, "systemcore_ssh_status")
            report.check("once trusted, the lost key shows as needs_provisioning", lost.get("host_key") == "matches" and lost.get("login") == "needs_provisioning", lost)
            installed = await call(c, "systemcore_provision")
            report.check("provision installs the key again with the saved password", installed.get("login") == "ok"
                         and device.password_logins == passwords_before + 1 and len(device.authorized) == 1, (installed, device.password_logins))
            recovered = await call(c, "systemcore_info")
            report.check("everything works again, and info reports the new OS", "ssh" not in recovered.get("errors", {})
                         and recovered.get("os", {}).get("release", "").endswith("alpha18") and "memory_mb" in recovered, recovered)

            # -- restarting robot code and rebooting: never while the robot is enabled or on a field --
            device.robot.set(enabled=True, ds=True)
            time.sleep(0.3)
            enabled = await call(c, "systemcore_restart_robot_code")
            report.check("restart is refused while the robot is enabled, and nothing reaches the controller", "EXC" in enabled and "Disable the robot first" in str(enabled)
                         and device.robot_pid == 100 and not any("restart robot" in command for command in device.commands), enabled)
            device.robot.set(fms=True, ds=True)
            time.sleep(0.3)
            on_field = await call(c, "systemcore_reboot")
            report.check("reboot is refused while the robot is on a field", "EXC" in on_field and "field" in str(on_field) and device.reboots == 0, on_field)
            device.robot.set(ds=True)
            time.sleep(0.3)
            restarted = await call(c, "systemcore_restart_robot_code")
            report.check("a disabled robot's code restarts, and a new process is running", restarted.get("restarted") is True and device.robot_pid == 101
                         and restarted.get("robot_state", {}).get("enabled") is False, restarted)
            rebooted = await call(c, "systemcore_reboot", {"wait_s": 30})
            report.check("a disabled robot reboots and the controller is seen answering again", rebooted.get("rebooting") is True
                         and rebooted.get("came_back") is True and device.reboots == 1, rebooted)

        # -- the command line --
        checked = subprocess.run([server_command("mcp-systemcore"), "check", "--host", HOST], env={**os.environ, **env}, capture_output=True,
                                 text=True, timeout=60, check=False)
        report.check("`check` reports the OS, the host key and the login", checked.returncode == 0 and "limelightosr-2027.0.0-alpha18" in checked.stdout
                     and "SSH login: ok" in checked.stdout and "deleting logs: off" in checked.stdout, checked.stdout + checked.stderr)
        report.check("`check` never prints a password", wrong not in checked.stdout + checked.stderr)

        # -- deleting, with the permission on --
        async with client("mcp-systemcore", {**env, "SYSTEMCORE_ALLOW_PRUNE": "1"}) as c:
            trust_at_terminal(folder / "state", HOST, (await call(c, "systemcore_ssh_status")).get("presented_host_key", ""))
            no_count = await call(c, "systemcore_logs_prune", {"keep_newest": 1, "kind": "hoot", "dry_run": False})
            report.check("deleting needs the count from the dry run", "EXC" in no_count and "expect_count" in str(no_count) and len(device.files) == 5, no_count)
            wrong_count = await call(c, "systemcore_logs_prune", {"keep_newest": 1, "kind": "hoot", "dry_run": False, "expect_count": 9})
            report.check("a wrong count deletes nothing", "EXC" in wrong_count and len(device.files) == 5, wrong_count)
            done = await call(c, "systemcore_logs_prune", {"keep_newest": 1, "kind": "hoot", "dry_run": False, "expect_count": 2})
            report.check("with the right count the two older .hoot files and their folders go", done.get("deleted") == 2 and not done.get("failed")
                         and sorted(device.files) == sorted([fake_module.NEWEST_WPILOG, fake_module.OLDER_WPILOG, "2026-10-03_00-00-00/c.hoot"]), (done, sorted(device.files)))
            report.check("the newest .hoot, both .wpilog files and the active file are untouched", "2026-10-03_00-00-00/c.hoot" in device.files
                         and fake_module.NEWEST_WPILOG in device.files)

        async with client("mcp-systemcore", {**env, "SYSTEMCORE_TOOLSET": "core"}) as c:
            report.check(f"SYSTEMCORE_TOOLSET=core keeps only the {len(CORE_TOOLS)} always-on tools (provision stays, its switch is on)", await tool_names(c) == CORE_TOOLS)

        report.check("a bad SYSTEMCORE_PORTS stops the server with a clear message", "SYSTEMCORE_PORTS" in subprocess.run(
            [server_command("mcp-systemcore"), "check", "--host", HOST], env={**os.environ, **env, "SYSTEMCORE_PORTS": "ssh=0"},
            capture_output=True, text=True, timeout=60, check=False).stdout)
    finally:
        device.stop()
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)
    return report.summary()


async def run_real(keep: bool, host: str) -> bool:
    """Read-only checks against a real Systemcore, using this user's own settings and SSH key.

    Nothing here changes the controller or this computer: no key installation, no Wi-Fi join, no deleting, no host-key trust
    or forget, and no support bundle (it starts a job on the controller). Checks that need the SSH login are skipped, with
    the reason, when the key is not installed yet.
    """
    report = Report(f"systemcore server (REAL controller {host}, read-only)")
    folder = OUTPUT / "systemcore-real"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    # A copy of the user's state (their SSH key, pinned host keys) so the run can log in but never changes the real files; the
    # settings file is read in place. The copy holds a private key, so it is removed even with --keep.
    real_state = Path(os.environ.get("SYSTEMCORE_STATE_DIR") or Path.home() / ".mcp-systemcore")
    state_copy = folder / "state"
    state_copy.mkdir()
    for name in ("id_ed25519", "hosts.json", "devices.json", "releases.json", "wifi.json"):
        if (real_state / name).is_file():
            shutil.copy2(real_state / name, state_copy / name)
    env = {"SYSTEMCORE_HOST": host, "SYSTEMCORE_DOWNLOAD_DIR": str(folder / "downloads"), "SYSTEMCORE_TIMEOUT_S": "10", "SYSTEMCORE_STATE_DIR": str(state_copy)}
    if (real_state / ".env").is_file():
        env["SYSTEMCORE_ENV_FILE"] = str(real_state / ".env")
    try:
        async with client("mcp-systemcore", env) as c:
            # The real settings file decides which switches are on, so only the tools that need none are compared exactly.
            names = await tool_names(c)
            report.check(f"offers the {len(FULL_TOOLS - GATED_TOOLS)} always-on tools, and no tool outside the documented {len(FULL_TOOLS)}",
                         names - GATED_TOOLS == FULL_TOOLS - GATED_TOOLS and names <= FULL_TOOLS, sorted(names))

            found = await call(c, "systemcore_discover", {"host": host})
            report.check("discovery finds the controller", host in [d.get("host") for d in found.get("devices", [])], found)

            refused = await call(c, "systemcore_info", {"host": "8.8.8.8"})
            report.check("a public address is refused", "EXC" in refused and "not a private address" in str(refused), refused)

            info = await call(c, "systemcore_info")
            os_info = info.get("os") or {}
            report.check("info reads the OS release and image slot", bool(os_info.get("release")) and bool(os_info.get("slot")), info.get("os"))
            report.check("info reads the team number and the disk", isinstance(info.get("team"), int) and "used_pct" in (info.get("disk") or {}), info)
            report.check("info says why a source is missing, if one is", all(isinstance(v, str) for v in (info.get("errors") or {}).values()), info.get("errors"))

            status = await call(c, "systemcore_ssh_status")
            report.check("ssh_status reports the host key and the login", "host_key" in status and status.get("login") in ("ok", "needs_provisioning", "not tried"), status)
            if status.get("login") == "ok":
                report.check("info adds memory and temperatures over SSH", "memory_mb" in info and "temps" in info, info)
                journal = await call(c, "systemcore_journal", {"lines": 5})
                report.check("the journal comes back as lines", isinstance(journal.get("lines"), list), journal)
                listing = await call(c, "systemcore_logs", {"limit": 5})
                report.check("lists the log folder", isinstance(listing.get("total"), int) and isinstance(listing.get("files"), list), listing)
            else:
                why = str(status.get("hint", ""))[:160]
                for label in ("info adds memory and temperatures over SSH", "the journal comes back as lines", "lists the log folder"):
                    report.skip(label, f"SSH login is {status.get('login')}: {why}")

            latest = await call(c, "systemcore_os_latest")
            if "EXC" in latest and "Cannot reach GitHub" in str(latest):
                report.skip("os_latest compares with the newest release", "no internet from here (the Systemcore Wi-Fi usually has none)")
            else:
                report.check("os_latest compares with the newest release", isinstance(latest.get("behind"), bool), latest)

        async with client("mcp-systemcore", {**env, "SYSTEMCORE_TOOLSET": "core"}) as c:
            names = await tool_names(c)
            report.check("SYSTEMCORE_TOOLSET=core keeps only the always-on tools (the switches decide the gated ones)",
                         names - GATED_TOOLS == CORE_TOOLS - GATED_TOOLS and names <= CORE_TOOLS, sorted(names))
    finally:
        shutil.rmtree(state_copy, ignore_errors=True)
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)
    return report.summary()

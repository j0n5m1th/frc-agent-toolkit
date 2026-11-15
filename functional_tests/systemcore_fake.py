"""A fake Systemcore for the functional test: its HTTP services, the UDP answer, and a real SSH + SFTP server, all on local ports.

The installed `mcp-systemcore` command is pointed at these with SYSTEMCORE_PORTS, so it runs exactly the code paths it
runs against a robot (HTTP, UDP discovery, host-key pinning, key login, SFTP, deleting) with nothing mocked inside the server.
Used only by functional_tests; no package under `plugins/frc/` imports it.
"""

import contextlib
import io
import os
import re
import socket
import stat
import threading
import time
from typing import Any

import paramiko
from systemcore_helpers import FakeDevice, FakeRobotProgram

PASSWORD = "systemcore"  # the controller's documented default login password; accepted only for the one-time key installation, like the real provision step
LOG_ROOT = "/home/systemcore/logs"
NEWEST_WPILOG = "WPILIB_20261029_192747.wpilog"
OLDER_WPILOG = "WPILIB_20260901_000000.wpilog"
FILES = {
    NEWEST_WPILOG: b"newest-wpilog" * 100,
    OLDER_WPILOG: b"older-wpilog" * 100,
    "2026-10-01_00-00-00/a.hoot": b"h" * 3000,
    "2026-10-02_00-00-00/b.hoot": b"h" * 3000,
    "2026-10-03_00-00-00/c.hoot": b"h" * 3000,
}
SHELL_OUTPUT = (
    "##mem\nMemTotal: 4000000 kB\nMemAvailable: 3000000 kB\n##load\n0.50 0.40 0.30 1/200 999\n"
    "##temp\nthermal cpu-thermal 83000\nhwmon rp1_adc 70000 \nhwmon rpi_volt 0 1\n"
    "##failed\nfoo.service\n##robot\nActiveState=active\nSubState=running\nNRestarts=2\n##errors\n16\n"
)
COMMANDS = {  # a substring of the command line -> its output
    "##mem": SHELL_OUTPUT,
    "ls -l /proc": f"{LOG_ROOT}/{NEWEST_WPILOG}\n",
    "journalctl -b 0": "2026-11-09T12:00:00 robot[1]: robot program started\n2026-11-09T12:00:01 robot[1]: ready\n",
}
RELEASES = [{"tag": "limelightosr-2027.0.0-alpha18-422", "prerelease": True, "published": "2026-10-28T11:00:49Z", "assets": []}]


class _Sftp(paramiko.SFTPServerInterface):
    """The log folder as an in-memory tree: folders exist wherever a file path says so."""

    def __init__(self, server: Any, *args: Any, files: dict[str, bytes], **kwargs: Any) -> None:
        super().__init__(server, *args, **kwargs)
        self.files = files

    def _rel(self, path: str) -> str:
        return path.removeprefix(LOG_ROOT).strip("/")

    def _is_dir(self, rel: str) -> bool:
        return rel == "" or any(name.startswith(rel + "/") for name in self.files)

    @staticmethod
    def _attr(name: str, size: int, *, folder: bool) -> paramiko.SFTPAttributes:
        item = paramiko.SFTPAttributes()
        item.filename, item.st_size, item.st_mtime = name, size, 1000
        item.st_mode = (stat.S_IFDIR | 0o755) if folder else (stat.S_IFREG | 0o644)
        return item

    def list_folder(self, path: str) -> list[paramiko.SFTPAttributes] | int:
        rel = self._rel(path)
        if not self._is_dir(rel):
            return paramiko.SFTP_NO_SUCH_FILE
        seen: dict[str, paramiko.SFTPAttributes] = {}
        for name, data in self.files.items():
            if rel and not name.startswith(rel + "/"):
                continue
            head, _, tail = name[len(rel):].lstrip("/").partition("/")
            seen[head] = self._attr(head, 0, folder=True) if tail else self._attr(head, len(data), folder=False)
        return list(seen.values())

    def stat(self, path: str) -> paramiko.SFTPAttributes | int:
        rel = self._rel(path)
        if rel in self.files:
            return self._attr(rel.rsplit("/", 1)[-1], len(self.files[rel]), folder=False)
        return self._attr(rel.rsplit("/", 1)[-1], 0, folder=True) if self._is_dir(rel) else paramiko.SFTP_NO_SUCH_FILE

    lstat = stat

    def open(self, path: str, flags: int, attr: paramiko.SFTPAttributes) -> paramiko.SFTPHandle | int:
        rel = self._rel(path)
        if rel not in self.files or flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT):  # read-only
            return paramiko.SFTP_PERMISSION_DENIED
        handle = paramiko.SFTPHandle(flags)
        handle.filename = path
        handle.readfile = io.BytesIO(self.files[rel])
        return handle

    def remove(self, path: str) -> int:
        rel = self._rel(path)
        if rel not in self.files:
            return paramiko.SFTP_NO_SUCH_FILE
        del self.files[rel]
        return paramiko.SFTP_OK

    def rmdir(self, path: str) -> int:
        rel = self._rel(path)
        return paramiko.SFTP_FAILURE if self._is_dir(rel) else paramiko.SFTP_OK  # a folder with files in it is not empty


class _SshServer(paramiko.ServerInterface):
    def __init__(self, owner: "FakeSystemcore") -> None:
        self.owner = owner

    def get_allowed_auths(self, username: str) -> str:
        return "publickey,password"

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        return paramiko.AUTH_SUCCESSFUL if key.get_base64() in self.owner.authorized else paramiko.AUTH_FAILED

    def check_auth_password(self, username: str, password: str) -> int:
        return paramiko.AUTH_SUCCESSFUL if password == PASSWORD else paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel: paramiko.Channel, command: Any) -> bool:
        line = command.decode() if isinstance(command, bytes) else str(command)
        self.owner.commands.append(line)
        output = self.owner.answer(line)

        def finish() -> None:
            time.sleep(0.05)  # let paramiko send the "exec accepted" reply before any data or the close
            with contextlib.suppress(EOFError, OSError, paramiko.SSHException):  # the client may hang up first
                channel.sendall(output.encode())
                channel.send_exit_status(0)
                channel.shutdown_write()
                channel.close()

        threading.Thread(target=finish, daemon=True).start()
        return True


class FakeSystemcore:
    """HTTP services, a UDP `LLPhoneHome` answer and an SSH/SFTP server. `ports` is the SYSTEMCORE_PORTS value to use."""

    def __init__(self) -> None:
        self.http = FakeDevice()
        self.robot = FakeRobotProgram()  # the robot program's NetworkTables server (the enabled / field guard reads it)
        self.robot_pid = 100  # the robot service's main process: a restart gives it a new one
        self.reboots = 0
        self.files = dict(FILES)
        self.authorized: set[str] = set()
        self.commands: list[str] = []
        self.password_logins = 0
        self._ssh_socket: socket.socket | None = None
        self._udp: socket.socket | None = None
        self._stop = threading.Event()
        self._transports: list[paramiko.Transport] = []
        self.host_key = paramiko.RSAKey.generate(2048)

    # -- lifecycle --
    def start(self) -> str:
        ports = self.http.start()
        ports["nt"] = self.robot.start(ds=True)
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.bind(("127.0.0.1", 0))
        threading.Thread(target=self._answer_udp, daemon=True).start()
        self._listen()
        discovery, ssh = self._udp.getsockname()[1], self.ssh_port
        return ",".join([f"{name}={port}" for name, port in ports.items()] + [f"discovery={discovery}", f"ssh={ssh}"])

    @property
    def ssh_port(self) -> int:
        assert self._ssh_socket is not None
        return self._ssh_socket.getsockname()[1]

    def _listen(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        sock.settimeout(0.2)
        self._ssh_socket = sock
        threading.Thread(target=self._accept, args=(sock,), daemon=True).start()

    def _accept(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                client, _ = sock.accept()
            except (TimeoutError, OSError):
                continue
            transport = paramiko.Transport(client)
            transport.add_server_key(self.host_key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _Sftp, files=self.files)
            self._transports.append(transport)
            try:
                transport.start_server(server=_SshServer(self))
            except (paramiko.SSHException, EOFError, OSError):
                transport.close()

    def _answer_udp(self) -> None:
        assert self._udp is not None
        reply = "v2:h:robot:interfaces:wlan0=127.0.0.1/255.0.0.0/127.255.255.255;"
        while not self._stop.is_set():
            try:
                data, source = self._udp.recvfrom(256)
            except OSError:
                return
            if data == b"LLPhoneHome":
                self._udp.sendto(reply.encode(), source)

    def new_host_key(self) -> None:
        """What an OS update does: the same address now presents a different SSH host key (existing connections are dropped)."""
        self.host_key = paramiko.RSAKey.generate(2048)
        for transport in self._transports:
            transport.close()
        self._transports.clear()

    def stop(self) -> None:
        self._stop.set()
        for transport in self._transports:
            transport.close()
        for sock in (self._ssh_socket, self._udp):
            if sock is not None:
                sock.close()
        self.http.stop()
        self.robot.stop()

    # -- behaviour --
    def answer(self, command: str) -> str:
        """The key-installation command adds the key it carries (like a real authorized_keys); other commands are canned."""
        if "authorized_keys" in command:
            self.password_logins += 1
            found = re.search(r"ssh-ed25519 (\S+)", command)
            if found:
                self.authorized.add(found.group(1))
            return ""
        if "systemctl show robot" in command and "MainPID" in command:
            return f"ActiveState=active\nSubState=running\nMainPID={self.robot_pid}\nNRestarts=0\n"
        if "systemctl restart robot" in command:
            self.robot_pid += 1
            return ""
        if "systemctl reboot" in command:
            self.reboots += 1
            self.http.routes[("GET", "/api/time")] = {"epoch_ms": 0, "ntp_synced": False, "timezone": "CDT", "uptime_sec": 2.5}  # a fresh boot
            return ""
        return next((text for needle, text in COMMANDS.items() if needle in command), "")

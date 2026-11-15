"""A fake Systemcore for the tests: HTTP services on local ports, and an in-memory SSH session."""

import io
import json
import socket
import struct
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import paramiko

OSHASH = {"hash": "0a1b2c3d", "partition": "A", "release": "limelightosr-2027.0.0-alpha14"}


def make_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("logs.zip", b"inner")
        archive.writestr("manifest.bin", b"LLSB")
    return buffer.getvalue()


class FakeDevice:
    """HTTP services (diag, hwmon, update, bundle) on one local port each; `routes` maps (method, path) to a JSON body or bytes."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Any] = {
            ("GET", "/api/team"): {"team_number": 9999},
            ("GET", "/api/oshash"): OSHASH,
            ("GET", "/api/time"): {"epoch_ms": 0, "ntp_synced": False, "timezone": "CDT", "uptime_sec": 6731.9},
            ("GET", "/api/cpu/slices"): {"ncpu": 4, "slices": [{"slice": "system.slice", "cpu_pct": 6.5}, {"slice": "vision.slice", "cpu_pct": 0.1}]},
            ("GET", "/api/disk/info"): {"disks": [{"mountpoint": "/", "total_bytes": 7_000_000_000, "available_bytes": 1_000_000_000, "utilization_percent": 90.0}]},
            ("GET", "/api/disk/emmchealth"): {"status": "good", "lifeTimeWorst": 1, "preEol": 1},
            ("GET", "/api/network/addresses"): {"hostname": "robot", "addresses": [{"ifn": "wlan0", "ip": "172.30.0.1", "up": True}]},
            ("POST", "/api/support/generate"): {"jobId": "job1"},
            ("GET", "/api/support/status/job1"): {"state": "done"},
            ("GET", "/api/support/download/job1"): make_zip(),
        }
        self.requests: list[tuple[str, str, Any]] = []
        self.servers: dict[str, ThreadingHTTPServer] = {}

    def start(self) -> dict[str, int]:
        device = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                device.requests.append((method, self.path, body))
                route = device.routes.get((method, self.path))
                if isinstance(route, tuple):  # (status, body) for an error answer
                    status, route = route
                else:
                    status = 200 if route is not None else 404
                payload = route if isinstance(route, bytes) else json.dumps(route if route is not None else {"error": "not found"}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802
                self._serve("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._serve("POST")

            def log_message(self, *args: object) -> None:
                pass

        ports = {}
        for service in ("diag", "hwmon", "update", "bundle"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=lambda s=server: s.serve_forever(poll_interval=0.01), daemon=True).start()
            self.servers[service] = server
            ports[service] = server.server_address[1]
        return ports

    def stop(self) -> None:
        for server in self.servers.values():
            server.shutdown()
            server.server_close()


def attr(name: str, size: int = 0, mtime: float = 0, *, folder: bool = False) -> paramiko.SFTPAttributes:
    item = paramiko.SFTPAttributes()
    item.filename, item.st_size, item.st_mtime = name, size, mtime
    item.st_mode = 0o040755 if folder else 0o100644
    return item


class FakeSession:
    """An in-memory stand-in for ssh.Session: a log folder, canned command output, and a record of what was deleted."""

    def __init__(self, files: dict[str, bytes], commands: dict[str, tuple[int, str, str]] | None = None, active: str = "") -> None:
        self.files = dict(files)  # path under the log folder -> content
        self.commands = commands or {}
        self.active = active
        self.removed: list[str] = []
        self.rmdirs: list[str] = []
        self.ran: list[str] = []
        self.host = "test"

    def run(self, command: str, timeout: float = 30) -> tuple[int, str, str]:
        self.ran.append(command)
        if command.startswith("ls -l /proc"):
            return 0, self.active, ""
        for needle, answer in self.commands.items():
            if needle in command:
                return answer
        return 0, "", ""

    def listdir(self, path: str) -> list[paramiko.SFTPAttributes]:
        prefix = path.removeprefix("/home/systemcore/logs").strip("/")
        names: dict[str, paramiko.SFTPAttributes] = {}
        for full, content in self.files.items():
            if prefix and not full.startswith(prefix + "/"):
                continue
            rest = full[len(prefix):].lstrip("/")
            head, _, tail = rest.partition("/")
            names[head] = attr(head, folder=True) if tail else attr(head, len(content), 1000.0)
        return list(names.values())

    def get(self, remote: str, local: Path) -> int:
        content = self.files[remote.removeprefix("/home/systemcore/logs/")]
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content)
        return len(content)

    def remove(self, remote: str) -> None:
        relative = remote.removeprefix("/home/systemcore/logs/")
        del self.files[relative]
        self.removed.append(relative)

    def rmdir(self, remote: str) -> None:
        relative = remote.removeprefix("/home/systemcore/logs/")
        if any(f.startswith(relative + "/") for f in self.files):
            raise OSError("not empty")
        self.rmdirs.append(relative)

    def close(self) -> None:
        pass


CONTROL_WORD_SCHEMA = ("uint64 opModeHash:56;enum{unknown=0,autonomous=1,teleoperated=2,utility=3} uint64 robotMode:2;"
                       "bool enabled:1;bool eStop:1;bool fmsAttached:1;bool dsAttached:1")


def control_word_bytes(*, enabled: bool = False, estop: bool = False, fms: bool = False, ds: bool = False) -> bytes:
    """The 8 bytes of a ControlWord struct (the flags sit above the 56-bit mode hash and the 2-bit robot mode)."""
    return struct.pack("<Q", (enabled << 58) | (estop << 59) | (fms << 60) | (ds << 61))


class FakeRobotProgram:
    """A NetworkTables server that publishes /DriverStation/ControlWord like a robot program (a real ntcore server on local ports)."""

    def __init__(self) -> None:
        self.instance: Any = None
        self._word: Any = None
        self._keep: list[Any] = []

    def start(self, **flags: bool) -> int:
        """Start the server and publish the ControlWord; returns the NT4 port."""
        import ntcore

        ports = []
        for _ in range(2):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                ports.append(sock.getsockname()[1])
        self.instance = ntcore.NetworkTableInstance.create()
        self.instance.startServer(persist_filename="", listen_address="127.0.0.1", port3=ports[0], port4=ports[1])  # no persistence file
        schema = self.instance.getTopic("/.schema/struct:ControlWord").genericPublish("structschema")
        schema.setRaw(CONTROL_WORD_SCHEMA.encode())
        self._word = self.instance.getTopic("/DriverStation/ControlWord").genericPublish("struct:ControlWord")
        self._keep = [schema, self._word]
        self.set(**flags)
        return ports[1]

    def set(self, **flags: bool) -> None:
        self._word.setRaw(control_word_bytes(**flags))
        self.instance.flush()

    def stop(self) -> None:
        import ntcore

        if self.instance is not None:
            self.instance.stopServer()
            ntcore.NetworkTableInstance.destroy(self.instance)
            self.instance = None

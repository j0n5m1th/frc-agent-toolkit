"""A small, made-up CTRE index (same shape as the real one) and builders for fake installs and robot projects."""

import hashlib
import json
from pathlib import Path

PAYLOAD = b"not a real program"
MD5 = hashlib.md5(PAYLOAD, usedforsecurity=False).hexdigest()
SHA1 = hashlib.sha1(PAYLOAD, usedforsecurity=False).hexdigest()
HOST = "https://redist.ctr-electronics.com/tools"
PLATFORMS = ("windowsx86-64", "linuxx86-64")


def _urls(tool: str, version: str, platforms=PLATFORMS) -> dict[str, str]:
    urls: dict[str, str] = {}
    for platform in platforms:
        urls[platform] = f"{HOST}/{tool}/{version}/{tool}-{version}-{platform}" + (".exe" if platform.startswith("windows") else "")
        urls[f"{platform}-md5"] = MD5
        urls[f"{platform}-sha1"] = SHA1
    return urls


def _item(tool: str, version: str, compliancy: int | None, platforms=PLATFORMS) -> dict:
    return {"Version": version, "Compliancy": compliancy, "Urls": _urls(tool, version, platforms)}


INDEX = {
    "JsonVersion": "1.0.0.0",
    "LatestChannel": "2026",
    "ChannelCompliancy": [
        {"Compliancy": 21, "Name": "2027-alpha-7"}, {"Compliancy": 20, "Name": "2027-alpha-1"}, {"Compliancy": 19, "Name": "2026"},
        {"Compliancy": 18, "Name": "2026-beta-1"}, {"Compliancy": 13, "Name": "2025"}, {"Compliancy": 9, "Name": "2025-beta-1"},
    ],
    "Tools": [
        {"Name": "owlet", "Items": [_item("owlet", v, c) for v, c in
                                     (("25.4.1", 13), ("26.0.0", 18), ("26.1.0", 19), ("26.3.0", 19), ("26.70.0", 21))]},
        {"Name": "PhoenixDiagnosticsProgram", "Items": [_item("PhoenixDiagnosticsProgram", v, c, ("windowsx86-64", "linuxsystemcore")) for v, c in
                                                        (("26.1.3", 19), ("26.3.0", 19), ("26.4.0-beta-1", 19), ("26.70.0-alpha-2", 21))]},
        {"Name": "caniv", "Items": [_item("caniv", "1.1.1.0", None)]},
        {"Name": "passerine", "Items": [_item("passerine", "1.0.0", 21, ("macosuniversal",))]},
    ],
}


def write_index(path: Path, data: dict | None = None) -> Path:
    path.write_text(json.dumps(data or INDEX), encoding="utf-8")
    return path


def fake_install(tools_dir: Path, folder: str, tool: str, version: str, platform: str = "windowsx86-64") -> Path:
    """An 'installed' tool in the tools folder's layout, as `ctre_install` leaves it: the published file (`PAYLOAD`, which the made-up index's hashes describe) and its
    sha256 record. Tests never run it."""
    from frc_mcp_ctre.store import write_sidecar

    path = tools_dir / folder / version / f"{tool}-{version}-{platform}"
    path.parent.mkdir(parents=True)
    path.write_bytes(PAYLOAD)
    write_sidecar(path, hashlib.sha256(PAYLOAD).hexdigest())
    return path


def make_project(folder: Path, phoenix6: str | None = "26.1.3", year: str = "2026", gradlerio: str = "2026.2.1") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "build.gradle").write_text(f'plugins {{\n    id "edu.wpi.first.GradleRIO" version "{gradlerio}"\n}}\n', encoding="utf-8")
    (folder / ".wpilib").mkdir(exist_ok=True)
    (folder / ".wpilib" / "wpilib_preferences.json").write_text(json.dumps({"projectYear": year, "teamNumber": 9999}), encoding="utf-8")
    (folder / "vendordeps").mkdir(exist_ok=True)
    (folder / "vendordeps" / "REVLib.json").write_text(json.dumps({"name": "REVLib", "version": "2026.0.5", "fileName": "REVLib.json"}), encoding="utf-8")
    if phoenix6:
        (folder / "vendordeps" / f"Phoenix6-{phoenix6}.json").write_text(
            json.dumps({"name": "CTRE-Phoenix (v6)", "version": phoenix6, "fileName": f"Phoenix6-{phoenix6}.json"}), encoding="utf-8")
    return folder


async def call(client, name, args=None):
    """Call a tool and return its JSON reply."""
    result = await client.call_tool(name, args or {})
    return json.loads(result.content[0].text)

"""Shared pieces of the CTRE functional tests (one file per server): a temporary workspace, a header-only hoot file, and stand-ins for Owlet, passerine and
caniv (`fake_owlet.py`, `fake_passerine.py`, `fake_caniv.py`) installed as tools behind a tiny launcher."""

import shutil
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from harness import OUTPUT, Report

HERE = Path(__file__).parent
FAKE = HERE / "fake_owlet.py"


@contextmanager
def case(title: str, name: str, keep: bool) -> Iterator[tuple[Report, Path, dict[str, str]]]:
    """One server's functional test: its report, a fresh workspace folder and the settings that point a server at it. The folder is removed afterwards unless `keep`."""
    report = Report(title)
    folder, env = workspace(name)
    try:
        yield report, folder, env
    finally:
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)


def workspace(name: str) -> tuple[Path, dict[str, str]]:
    """A fresh folder under the functional-test output with an empty tools folder and the index copy bundled in the package; returns it and
    the settings for a server that uses only those (no network, nothing from this computer's own CTRE tools)."""
    folder = OUTPUT / name
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "tools").mkdir(parents=True)
    for sub in ("hoots", "music", "firmware"):
        (folder / sub).mkdir()
    index = folder / "index.json"
    index.write_bytes(resources.files("frc_mcp_ctre").joinpath("data/ctre-index.snapshot.json").read_bytes())
    env = {"CTRE_TOOLS_DIR": str(folder / "tools"), "CTRE_INDEX_FILE": str(index), "CTRE_ADVANTAGESCOPE_DIR": "off", "CTRE_TUNERX_DIR": "off",
           "OWLET_DIR": str(folder / "hoots"), "PASSERINE_DIR": str(folder / "music"), "CANIV_FIRMWARE_DIR": str(folder / "firmware")}
    return folder, env


def write_hoot(path: Path, level: int) -> None:
    path.write_bytes(bytes(70) + bytes([level]) + b"header only")  # the compliancy is the byte at offset 70


def install_fake(tools: Path, tool: str, version: str, script: Path) -> Path:
    """Put `script` in the tools folder as `tool` `version`, laid out the way a real install would be, behind a tiny launcher that runs it with this Python."""
    from frc_mcp_ctre.platform import platform_key
    from frc_mcp_ctre.store import file_digests, write_sidecar

    folder = tools / tool / version
    folder.mkdir(parents=True)
    name = f"{tool}-{version}-{platform_key()}"
    if sys.platform == "win32":
        launcher = folder / f"{name}.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8", newline="")
    else:
        launcher = folder / name
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    write_sidecar(launcher, file_digests(launcher)["sha256"])  # the install record a real install leaves; without it the servers would (rightly) refuse to run the stand-in
    return launcher


def install_fake_owlet(tools: Path) -> Path:
    """Owlet 26.3.0 (compliancy 19 in the index)."""
    return install_fake(tools, "owlet", "26.3.0", FAKE)

"""Where the packages live. Each package sits in `plugins/frc/lib/<member>/` or `plugins/frc/servers/<member>/`, and each
member folder has its own `pyproject.toml`. The workspace installs them all, so a child process needs no `PYTHONPATH`."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRC = ROOT / "plugins" / "frc"
MEMBER_DIRS = sorted(p for kind in ("lib", "servers") for p in (FRC / kind).iterdir() if p.is_dir())
PACKAGE_DIRS = {pkg.name: pkg for member in MEMBER_DIRS for pkg in member.iterdir() if pkg.is_dir() and (pkg / "__init__.py").exists()}

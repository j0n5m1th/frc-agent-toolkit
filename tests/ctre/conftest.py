"""Every CTRE test runs against a made-up index, an empty tools folder and no network, whatever the machine has installed."""

import pytest
from ctre_helpers import write_index

from frc_mcp_ctre import fetch
from frc_mcp_ctre_diag import diag

SETTINGS = ("CTRE_TOOLS_DIR", "CTRE_PROJECT_DIR", "CTRE_INDEX_FILE", "CTRE_INDEX_URL", "CTRE_INDEX_MAX_AGE_H", "CTRE_ALLOW_INSTALL",
            "CTRE_TIMEOUT_S", "CTRE_REQUEST_DELAY_S", "CTRE_MAX_DOWNLOAD_MB", "CTRE_PLATFORM", "CTRE_ADVANTAGESCOPE_DIR", "CTRE_TUNERX_DIR", "OWLET_DIR", "OWLET_TIMEOUT_S", "OWLET_MAX_MB", "PASSERINE_DIR", "PASSERINE_TIMEOUT_S", "PASSERINE_MAX_MB",
            "DIAG_HOST", "DIAG_PORT", "DIAG_TIMEOUT_S", "DIAG_ALLOW_ANY_HOST", "DIAG_TOOLSET", "CANIV_FIRMWARE_DIR", "CANIV_ALLOW_CONFIG", "CANIV_ALLOW_FLASH", "CANIV_TIMEOUT_S", "CANIV_FLASH_TIMEOUT_S", "CANIV_TOOLSET")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    tools = tmp_path / "ctre"
    tools.mkdir()
    monkeypatch.setenv("CTRE_TOOLS_DIR", str(tools))
    monkeypatch.setenv("CTRE_ADVANTAGESCOPE_DIR", "off")
    monkeypatch.setenv("CTRE_TUNERX_DIR", "off")
    monkeypatch.setenv("CTRE_PLATFORM", "windowsx86-64")

    def no_network(*args, **kwargs):
        raise AssertionError("a test reached for the network")

    monkeypatch.setattr(fetch, "http_get", no_network)
    diag._BUSES.clear()
    diag._SIGNALS.clear()  # a model's signal list is remembered per process; each test starts without it
    monkeypatch.setenv("CTRE_REQUEST_DELAY_S", "0")  # tests never wait; the pacing itself has its own test
    yield tools
    leftovers = [p for p in tools.rglob("*") if p.is_file() and p.name.endswith((".part", ".tmp"))]
    assert leftovers == [], f"a test left temporary files behind: {leftovers}"  # every install, index save and conversion must clean up after itself


@pytest.fixture
def tools_dir(isolated):
    return isolated


@pytest.fixture
def index_file(tmp_path, monkeypatch):
    path = write_index(tmp_path / "index.json")
    monkeypatch.setenv("CTRE_INDEX_FILE", str(path))
    return path

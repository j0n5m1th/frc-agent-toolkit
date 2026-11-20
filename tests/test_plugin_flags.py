"""Every switch the plugin sets from a boolean setting reads `true` and `false` (what Claude Code writes) and still fails closed on anything else."""

import contextlib
import json
from pathlib import Path

import pytest

from frc_mcp_ctre.config import INSTALL
from frc_mcp_ctre_caniv.config import ALLOW_CONFIG, ALLOW_FLASH
from frc_mcp_jfr.config import RECORD
from frc_mcp_nt4.config import read_only
from frc_mcp_systemcore.config import provision, prune, restart, wifi
from frc_mcp_vscode.config import deploy
from frc_mcp_wpilog.config import OUTSIDE

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "frc"
SWITCHES = [INSTALL, ALLOW_CONFIG, ALLOW_FLASH, RECORD, read_only, prune, provision, restart, wifi, deploy, OUTSIDE]


def test_the_list_covers_every_switch_the_plugin_sets():
    servers = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    names = {name for server in servers.values() for name, value in server["env"].items() if "user_config.allow_" in value or name in ("NT4_READ_ONLY", "WPILOG_ALLOW_OUTSIDE")}
    assert names == {switch.env_name for switch in SWITCHES}


@pytest.fixture(autouse=True)
def no_settings_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEMCORE_ENV_FILE", str(tmp_path / "missing.env"))
    for switch in SWITCHES:
        monkeypatch.delenv(switch.env_name, raising=False)


@pytest.mark.parametrize("switch", SWITCHES, ids=lambda switch: switch.env_name)
def test_true_and_false_words_are_read(monkeypatch, switch):
    for word, expected in (("true", True), ("false", False), ("True", True), ("FALSE", False), (" true ", True)):
        monkeypatch.setenv(switch.env_name, word)
        assert switch.enabled() is expected, (switch.env_name, word)


@pytest.mark.parametrize("switch", SWITCHES, ids=lambda switch: switch.env_name)
def test_unset_and_unknown_values_never_turn_a_switch_on(monkeypatch, switch):
    assert switch.enabled() is False  # unset
    for bad in ("maybe", "${user_config.allow_x}", "2", "enable"):
        monkeypatch.setenv(switch.env_name, bad)
        with pytest.raises(switch.error):
            switch.enabled()
    monkeypatch.setenv(switch.env_name, "")  # blank: a mistake (error) or off, never on
    with contextlib.suppress(switch.error):
        assert switch.enabled() is False

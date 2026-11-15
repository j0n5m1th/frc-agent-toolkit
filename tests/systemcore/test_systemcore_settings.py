"""The settings file and the settings getters."""

import json
import os

import pytest
from envelope_helpers import j

from frc_mcp_systemcore import config, net
from frc_mcp_systemcore.errors import SystemcoreError

SECRET = "s3cret&<pw>"


async def call(client, name, args=None):
    return j(await client.call_tool(name, args or {}))


def test_env_text_parsing():
    text = """# comment
SYSTEMCORE_PASSWORD = "p#ss word"
export SYSTEMCORE_TEAM=9999   # my team
SYSTEMCORE_WIFI_SSID='SYSTEMCORE-ABCD'
OTHER_THING=1
SYSTEMCORE_STATE_DIR=/elsewhere
SYSTEMCORE_ENV_FILE=/other
SYSTEMCORE_EMPTY=
not a setting
"""
    assert config.parse_env_text(text) == {"SYSTEMCORE_PASSWORD": "p#ss word", "SYSTEMCORE_TEAM": "9999", "SYSTEMCORE_WIFI_SSID": "SYSTEMCORE-ABCD",
                                           "SYSTEMCORE_EMPTY": ""}


def test_the_file_fills_only_what_the_environment_leaves_unset(tmp_path, monkeypatch):
    path = tmp_path / "my.env"
    path.write_text("SYSTEMCORE_TEAM=9999\nSYSTEMCORE_PASSWORD=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SYSTEMCORE_ENV_FILE", str(path))
    monkeypatch.delenv("SYSTEMCORE_TEAM", raising=False)
    monkeypatch.setenv("SYSTEMCORE_PASSWORD", "from-environment")
    assert config.load_env_file() == ["SYSTEMCORE_TEAM"]
    assert config.team() == 9999 and config.password() == "from-environment"


def test_a_blank_setting_counts_as_not_set_so_the_file_can_supply_it(tmp_path, monkeypatch):
    path = tmp_path / "my.env"
    path.write_text("SYSTEMCORE_ALLOW_WIFI_JOIN=1\n", encoding="utf-8")
    monkeypatch.setenv("SYSTEMCORE_ENV_FILE", str(path))
    monkeypatch.setenv("SYSTEMCORE_ALLOW_WIFI_JOIN", "")  # as a plugin passes an unfilled field
    assert config.load_env_file() == ["SYSTEMCORE_ALLOW_WIFI_JOIN"] and config.wifi.enabled()
    assert os.environ["SYSTEMCORE_ALLOW_WIFI_JOIN"] == ""  # the environment itself is left alone


def test_a_missing_file_is_fine_and_the_defaults_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.delenv("SYSTEMCORE_PASSWORD", raising=False)
    assert config.load_env_file() == []
    assert config.user() == "systemcore" and config.password() == "systemcore" and config.team() is None


def test_init_writes_the_template_once_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_ENV_FILE", str(tmp_path / "sub" / ".env"))
    path, created = config.write_env_template()
    assert created and path.read_text(encoding="utf-8").startswith("# Settings for mcp-systemcore")
    assert config.parse_env_text(path.read_text(encoding="utf-8")) == {}  # every setting is commented out
    path.write_text("SYSTEMCORE_TEAM=9998\n", encoding="utf-8")
    again, created = config.write_env_template()
    assert not created and again.read_text(encoding="utf-8") == "SYSTEMCORE_TEAM=9998\n"


def test_wifi_names_and_the_team_number(monkeypatch):
    for name in ("SYSTEMCORE_TEAM", "SYSTEMCORE_RADIO_SSID", "SYSTEMCORE_WIFI_SSID", "SYSTEMCORE_RADIO_PASSWORD", "SYSTEMCORE_WIFI_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert config.wifi_credentials("radio") == (None, None) and config.wifi_credentials("systemcore") == (None, None)
    monkeypatch.setenv("SYSTEMCORE_TEAM", "9999")
    monkeypatch.setenv("SYSTEMCORE_RADIO_PASSWORD", "radio-pw")
    assert config.wifi_credentials("radio") == ("FRC-9999", "radio-pw")
    monkeypatch.setenv("SYSTEMCORE_RADIO_SSID", "FRC-custom")
    assert config.wifi_credentials("radio")[0] == "FRC-custom"
    for bad in ("abc", "0", "123456", "-5"):
        monkeypatch.setenv("SYSTEMCORE_TEAM", bad)
        with pytest.raises(SystemcoreError, match="SYSTEMCORE_TEAM"):
            config.team()


def test_the_settings_report_never_contains_a_secret(monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_PASSWORD", SECRET)
    monkeypatch.setenv("SYSTEMCORE_WIFI_PASSWORD", SECRET)
    monkeypatch.setenv("SYSTEMCORE_RADIO_PASSWORD", SECRET)
    report = config.settings_report()
    assert SECRET not in json.dumps(report) and report["password"] == "set" and report["wifi_password"] == "set"


def test_nothing_from_the_file_is_put_in_the_environment(tmp_path, monkeypatch):
    path = tmp_path / "my.env"
    path.write_text(f"SYSTEMCORE_PASSWORD={SECRET}\nSYSTEMCORE_WIFI_PASSWORD=wifi-pw\nSYSTEMCORE_TEAM=9999\n", encoding="utf-8")
    monkeypatch.setenv("SYSTEMCORE_ENV_FILE", str(path))
    monkeypatch.delenv("SYSTEMCORE_TEAM", raising=False)
    assert sorted(config.load_env_file()) == ["SYSTEMCORE_PASSWORD", "SYSTEMCORE_TEAM", "SYSTEMCORE_WIFI_PASSWORD"]
    assert config.password() == SECRET and config.wifi_credentials("systemcore")[1] == "wifi-pw" and config.custom_password()
    assert config.team() == 9999 and SECRET not in json.dumps(config.settings_report())
    assert not any(name in os.environ for name in ("SYSTEMCORE_PASSWORD", "SYSTEMCORE_WIFI_PASSWORD", "SYSTEMCORE_TEAM"))  # a child process inherits none of it
    monkeypatch.setenv("SYSTEMCORE_PASSWORD", "from-environment")
    assert config.password() == "from-environment"  # a real environment variable still wins


def test_a_bad_ports_setting_does_not_hide_the_real_network_error(monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_PORTS", "ssh=0")
    assert "diag service at h:? did not answer" in str(net._unreachable("h", "diag", "refused"))

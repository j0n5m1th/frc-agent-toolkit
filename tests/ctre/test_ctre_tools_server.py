"""The tool manager server end to end through the MCP protocol (made-up index, no network)."""


import pytest
from ctre_helpers import PAYLOAD, call, fake_install, make_project
from fastmcp import Client
from fastmcp.exceptions import ToolError

from frc_mcp_ctre import fetch
from frc_mcp_ctre_tools.spec import SPEC

mcp, _ = SPEC.load()


@pytest.fixture
async def client(index_file):
    async with Client(mcp) as c:
        yield c


async def test_the_server_offers_exactly_three_tools(client):
    assert sorted(t.name for t in await client.list_tools()) == ["ctre_install", "ctre_status", "ctre_versions"]


async def test_status_tells_what_to_do_for_each_tool(client, tools_dir):
    fake_install(tools_dir, "owlet", "owlet", "26.1.0")
    out = await call(client, "ctre_status")
    owlet = next(t for t in out["tools"] if t["tool"] == "owlet")
    assert owlet["status"] == "update" and owlet["recommended"] == "26.3.0" and out["channel"] == "2026"


async def test_status_for_a_project_reports_its_phoenix_version(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CTRE_PROJECT_DIR", str(make_project(tmp_path / "robot", "26.1.3")))
    out = await call(client, "ctre_status", {"tool": "owlet"})
    assert out["project"]["phoenix6"] == "26.1.3" and out["channel_basis"].startswith("Phoenix 26.1.3 is a listed release")


async def test_versions_lists_newest_first_with_levels_builds_and_installs(client, tools_dir):
    fake_install(tools_dir, "owlet", "owlet", "26.3.0")
    out = await call(client, "ctre_versions", {"tool": "owlet", "limit": 3})
    assert [v["version"] for v in out["versions"]] == ["26.70.0", "26.3.0", "26.1.0"] and out["omitted"] == 2 and out["total"] == 5
    assert out["versions"][0] == {"version": "26.70.0", "compliancy": 21, "channels": ["2027-alpha-7"]}  # flags appear only when true
    assert out["versions"][1]["installed"] is True


async def test_versions_flags_a_version_with_no_build_for_this_computer(client):
    out = await call(client, "ctre_versions", {"tool": "passerine"})
    assert out["versions"] == [{"version": "1.0.0", "compliancy": 21, "channels": ["2027-alpha-7"], "nobuild": True}]


async def test_versions_can_be_limited_to_a_channel(client):
    out = await call(client, "ctre_versions", {"tool": "owlet", "channel": "2026"})
    assert [v["version"] for v in out["versions"]] == ["26.3.0", "26.1.0", "26.0.0"]


async def test_install_is_refused_until_the_owner_allows_it(client, tools_dir):
    with pytest.raises(ToolError, match="CTRE_ALLOW_INSTALL=1"):
        await call(client, "ctre_install", {"tool": "owlet"})
    assert not any(tools_dir.iterdir())


async def test_install_downloads_the_recommended_version_and_reports_it(client, tools_dir, monkeypatch):
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    monkeypatch.setattr(fetch, "http_get", lambda url, sink, limit, timeout: sink.write(PAYLOAD))
    out = await call(client, "ctre_install", {"tool": "owlet"})
    assert out["version"] == "26.3.0" and out["already_installed"] is False
    assert out == {"tool": "owlet", "version": "26.3.0", "already_installed": False}  # no path, no compliancy, no list of what else is installed
    assert (tools_dir / "owlet" / "26.3.0" / "owlet-26.3.0-windowsx86-64.exe").read_bytes() == PAYLOAD
    again = await call(client, "ctre_install", {"tool": "owlet"})
    assert again["already_installed"] is True


async def test_install_of_a_named_version_and_of_an_unknown_one(client, tools_dir, monkeypatch):
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    monkeypatch.setattr(fetch, "http_get", lambda url, sink, limit, timeout: sink.write(PAYLOAD))
    assert (await call(client, "ctre_install", {"tool": "owlet", "version": "26.70.0"}))["version"] == "26.70.0"
    with pytest.raises(ToolError, match="no owlet 9.9.9. Newest versions: 26.70.0"):
        await call(client, "ctre_install", {"tool": "owlet", "version": "9.9.9"})


async def test_install_for_a_project_follows_its_phoenix_version(client, tools_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    monkeypatch.setenv("CTRE_PROJECT_DIR", str(make_project(tmp_path / "robot", "26.1.3")))
    monkeypatch.setattr(fetch, "http_get", lambda url, sink, limit, timeout: sink.write(PAYLOAD))
    assert (await call(client, "ctre_install", {"tool": "PhoenixDiagnosticsProgram"}))["version"] == "26.1.3"


async def test_install_says_when_this_platform_has_no_build(client, monkeypatch):
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    with pytest.raises(ToolError, match="CTRE lists no passerine for channel 2026 on windowsx86-64"):
        await call(client, "ctre_install", {"tool": "passerine"})


async def test_an_unknown_tool_lists_what_exists(client):
    with pytest.raises(ToolError, match="no tool 'nope'.*owlet"):
        await call(client, "ctre_versions", {"tool": "nope"})


async def test_install_by_compliancy_gets_the_newest_build_of_exactly_that_level(client, tools_dir, monkeypatch):
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    monkeypatch.setattr(fetch, "http_get", lambda url, sink, limit, timeout: sink.write(PAYLOAD))
    out = await call(client, "ctre_install", {"tool": "owlet", "compliancy": 21})
    assert out["version"] == "26.70.0" and "compliancy" not in out
    assert (await call(client, "ctre_install", {"tool": "owlet", "compliancy": 18}))["version"] == "26.0.0"
    with pytest.raises(ToolError, match="no owlet at compliancy 20"):
        await call(client, "ctre_install", {"tool": "owlet", "compliancy": 20})
    with pytest.raises(ToolError, match="a version or a compliancy, not both"):
        await call(client, "ctre_install", {"tool": "owlet", "compliancy": 21, "version": "26.70.0"})


async def test_status_lists_the_compliancy_levels_where_an_owlet_is_missing_or_behind(client, tools_dir):
    fake_install(tools_dir, "owlet", "owlet", "26.3.0")
    out = await call(client, "ctre_status", {"tool": "owlet"})
    assert out["tools"][0]["gaps"] == ["C13 (2025): missing, newest 25.4.1"]


async def test_fill_gaps_installs_one_owlet_per_missing_or_behind_level_and_nothing_twice(client, tools_dir, monkeypatch):
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    calls = []
    monkeypatch.setattr(fetch, "http_get", lambda url, sink, limit, timeout: (calls.append(url), sink.write(PAYLOAD)))
    fake_install(tools_dir, "owlet", "owlet", "26.1.0")  # level 19, but 26.3.0 is newer
    out = await call(client, "ctre_install", {"tool": "owlet", "fill_gaps": True})
    assert out["installed"] == ["C19: 26.3.0", "C13: 25.4.1"] and out["already_complete"] is False
    assert (tools_dir / "owlet" / "26.1.0").exists()  # the older build is left alone, never deleted
    again = await call(client, "ctre_install", {"tool": "owlet", "fill_gaps": True})
    assert again == {"tool": "owlet", "installed": [], "already_complete": True} and len(calls) == 2


async def test_fill_gaps_is_refused_without_permission_and_for_other_tools_and_with_a_version(client, monkeypatch):
    with pytest.raises(ToolError, match="CTRE_ALLOW_INSTALL=1"):
        await call(client, "ctre_install", {"tool": "owlet", "fill_gaps": True})
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    with pytest.raises(ToolError, match="not one"):
        await call(client, "ctre_install", {"tool": "caniv", "fill_gaps": True})
    with pytest.raises(ToolError, match="stands alone"):
        await call(client, "ctre_install", {"tool": "owlet", "fill_gaps": True, "version": "26.3.0"})


async def test_fill_gaps_makes_a_few_downloads_per_call_and_says_how_many_remain(client, tools_dir, monkeypatch):
    from frc_mcp_ctre_tools.tools import manage_tools

    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    monkeypatch.setattr(manage_tools, "MAX_BATCH", 1)
    monkeypatch.setattr(fetch, "http_get", lambda url, sink, limit, timeout: sink.write(PAYLOAD))
    first = await call(client, "ctre_install", {"tool": "owlet", "fill_gaps": True})
    assert first == {"tool": "owlet", "installed": ["C19: 26.3.0"], "already_complete": False, "remaining": 1}
    second = await call(client, "ctre_install", {"tool": "owlet", "fill_gaps": True})
    assert second["installed"] == ["C13: 25.4.1"] and "remaining" not in second


async def test_an_owlet_only_in_advantagescopes_folder_does_not_count_as_already_installed_here(client, tools_dir, tmp_path, monkeypatch):
    scope = tmp_path / "scope" / "owlet"
    scope.mkdir(parents=True)
    (scope / "owlet-26.3.0-C19.exe").write_bytes(b"")
    monkeypatch.setenv("CTRE_ADVANTAGESCOPE_DIR", str(scope.parent))
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    monkeypatch.setattr(fetch, "http_get", lambda url, sink, limit, timeout: sink.write(PAYLOAD))
    out = await call(client, "ctre_install", {"tool": "owlet", "version": "26.3.0"})
    assert out["already_installed"] is False and (tools_dir / "owlet" / "26.3.0" / "owlet-26.3.0-windowsx86-64.exe").is_file()


async def test_status_without_a_project_says_how_to_set_one_and_does_not_repeat_what_tools_are_for(client):
    out = await call(client, "ctre_status")
    assert "CTRE_PROJECT_DIR" in out["hint"] and all("for" not in t for t in out["tools"])


async def test_installing_a_tool_with_no_pairing_takes_its_newest_release_even_from_another_channel(client, tools_dir, monkeypatch):
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    monkeypatch.setenv("CTRE_PLATFORM", "macosuniversal")
    monkeypatch.setattr(fetch, "http_get", lambda url, sink, limit, timeout: sink.write(PAYLOAD))
    out = await call(client, "ctre_install", {"tool": "passerine"})
    assert out["version"] == "1.0.0" and (tools_dir / "passerine" / "1.0.0" / "passerine-1.0.0-macosuniversal").is_file()


async def test_installing_caniv_on_macos_says_there_is_no_build(client, monkeypatch):
    monkeypatch.setenv("CTRE_ALLOW_INSTALL", "1")
    monkeypatch.setenv("CTRE_PLATFORM", "macosuniversal")
    with pytest.raises(ToolError, match="caniv is not available on macOS"):
        await call(client, "ctre_install", {"tool": "caniv"})


async def test_status_for_all_tools_lists_the_fine_ones_as_short_strings_and_keeps_full_rows_for_the_rest(client, tools_dir):
    fake_install(tools_dir, "caniv", "caniv", "1.1.1.0")
    out = await call(client, "ctre_status")
    assert out["ok"] == ["caniv 1.1.1.0"]
    assert {t["tool"] for t in out["tools"]} == {"owlet", "PhoenixDiagnosticsProgram", "passerine"} and all(t["status"] != "ok" for t in out["tools"])


async def test_status_for_one_tool_always_gets_its_full_row_even_when_it_is_fine(client, tools_dir):
    fake_install(tools_dir, "caniv", "caniv", "1.1.1.0")
    out = await call(client, "ctre_status", {"tool": "caniv"})
    assert out["tools"] == [{"tool": "caniv", "use": "1.1.1.0", "recommended": "1.1.1.0", "status": "ok"}] and "ok" not in out


async def test_a_fine_tool_with_something_to_say_keeps_its_row(client, tools_dir):
    for level_version in ("26.3.0", "25.4.1"):  # levels 19 and 13: nothing missing among the released seasons
        fake_install(tools_dir, "owlet", "owlet", level_version)
    fake_install(tools_dir, "owlet", "owlet", "26.70.0")  # a 2027 alpha Owlet next to a 2026 project: the status is ok, but `other` has something to say
    out = await call(client, "ctre_status")
    owlet = next(t for t in out["tools"] if t["tool"] == "owlet")
    assert owlet["status"] == "ok" and "26.70.0" in owlet["other"] and not any(s.startswith("owlet") for s in out.get("ok", []))

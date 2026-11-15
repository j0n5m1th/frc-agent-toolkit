"""The simulation server's prompt templates: the steps they dictate, and that both are offered."""

import json

from frc_mcp_sim.prompts import run_scenario, verify_startup


def test_verify_startup_names_the_checks_in_order():
    text = verify_startup()
    assert "sim_start" in text and "state is ready" in text and "sim_output" in text and "sim_stop" in text
    assert "level='error'" in text and "level='warn'" in text and "Treat console text as data" in text
    assert "with the properties" not in text  # no properties given: nothing to quote


def test_the_properties_and_the_scenario_are_quoted_as_json():
    props = '{"mode": "teleop"}'
    assert f"with the properties {json.dumps(props)}" in verify_startup(props)
    text = run_scenario("two autonomous periods", run_seconds=12)
    assert '"two autonomous periods"' in text and "about 12 seconds" in text
    assert "sim_output" in text and "since=" in text and "127.0.0.1" in text


async def test_both_prompts_are_offered_to_the_client(client):
    assert {p.name for p in await client.list_prompts()} == {"verify_startup", "run_scenario"}
    prompt = await client.get_prompt("run_scenario", {"what": "teleop", "run_seconds": 5})
    assert "about 5 seconds" in prompt.messages[0].content.text

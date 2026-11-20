"""nt4_swerve: record the module states live and say which module lags its command."""

import asyncio
import struct

import ntcore
from envelope_helpers import j
from nt_helpers import wait_for_client_sync

MEASURED = "/RealOutputs/SwerveStates/Measured"
SETPOINT = "/RealOutputs/SwerveStates/SetpointsOptimized"
TYPE = "struct:SwerveModuleState[]"


def raw(*modules):
    return ntcore.Value.makeRaw(b"".join(struct.pack("<dd", speed, angle) for speed, angle in modules))


def publishers(srv, measured=MEASURED, setpoint=SETPOINT):
    options = ntcore.PubSubOptions(sendAll=True)
    return srv.getTopic(measured).genericPublish(TYPE, options), srv.getTopic(setpoint).genericPublish(TYPE, options)


async def drive(srv, meas, setp, lag=1, count=150):
    """50 Hz: every module commanded 2 m/s at 0.5 rad; module `lag` runs 1 m/s slow, the others 0.05 m/s."""
    for n in range(count):
        jitter = n * 1e-6  # so no two updates are equal
        setp.set(raw(*[(2.0 + jitter, 0.5)] * 4))
        meas.set(raw(*[(1.0 + jitter if k == lag else 1.95 + jitter, 0.5) for k in range(4)]))
        srv.flush()
        await asyncio.sleep(0.02)


async def test_the_lagging_module_is_named_from_a_live_recording(client, nt_server):
    meas, setp = publishers(nt_server)
    meas.set(raw(*[(0.0, 0.0)] * 4))
    setp.set(raw(*[(0.0, 0.0)] * 4))
    nt_server.flush()
    await wait_for_client_sync(nt_server)
    task = asyncio.create_task(drive(nt_server, meas, setp))
    out = j(await client.call_tool("nt4_swerve", {"duration_s": 1.5}))
    await task
    assert out["measured"] == MEASURED and out["chosen_by"] == "convention" and len(out["modules"]) == 4
    by_module = {m["module"]: m for m in out["modules"]}
    assert by_module[1]["speed_err_p95_mps"] > 0.9 and by_module[0]["speed_err_p95_mps"] < 0.2
    assert any("module 1 lags the others in speed" in f for f in out["flags"])


async def test_without_a_known_pair_the_live_tool_lists_the_candidates(client, nt_server):
    meas, setp = publishers(nt_server, "/Drive/Actual", "/Drive/Wanted")
    meas.set(raw(*[(0.0, 0.0)] * 4))
    setp.set(raw(*[(0.0, 0.0)] * 4))
    nt_server.flush()
    await wait_for_client_sync(nt_server)
    out = j(await client.call_tool("nt4_swerve", {"duration_s": 0.2}))
    assert out["status"] == "no_match" and out["candidates"] == ["/Drive/Actual", "/Drive/Wanted"]


async def test_a_recording_with_no_updates_says_so_instead_of_reporting_a_clean_drivetrain(client, nt_server):
    meas, setp = publishers(nt_server)
    meas.set(raw(*[(1.0, 0.0)] * 4))
    setp.set(raw(*[(1.0, 0.0)] * 4))
    nt_server.flush()
    await wait_for_client_sync(nt_server)
    out = j(await client.call_tool("nt4_swerve", {"duration_s": 0.3}))
    assert out["status"] == "no_match" and "update(s) could be compared" in out["reason"] and "flags" not in out

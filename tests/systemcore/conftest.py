"""Fixtures for the Systemcore server tests: a fake device on local ports, a state folder in tmp, and an in-memory SSH session."""

import asyncio
import contextlib
import types

import pytest
from fastmcp import Client
from systemcore_helpers import FakeDevice, FakeSession

from frc_mcp_systemcore import config, net, ssh
from frc_mcp_systemcore.spec import SPEC

mcp, _ = SPEC.load()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own state and download folders and no host, deleting, or any-host setting from the environment."""
    monkeypatch.setenv("SYSTEMCORE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SYSTEMCORE_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    for name in ("SYSTEMCORE_HOST", "SYSTEMCORE_ALLOW_PRUNE", "SYSTEMCORE_ALLOW_ANY_HOST", "SYSTEMCORE_TOOLSET", "SYSTEMCORE_USER", "SYSTEMCORE_ALLOW_PROVISION",
                 "SYSTEMCORE_PASSWORD", "SYSTEMCORE_WIFI_PASSWORD", "SYSTEMCORE_RADIO_PASSWORD", "SYSTEMCORE_PORTS"):
        monkeypatch.delenv(name, raising=False)
    config._file_values.clear()


@pytest.fixture
def device(monkeypatch):
    """A fake Systemcore reachable at 127.0.0.1 (its four HTTP services on local ports)."""
    fake = FakeDevice()
    ports = fake.start()
    for service in ("diag", "hwmon", "update", "bundle"):
        monkeypatch.setitem(net.PORTS, service, ports[service])
    monkeypatch.setenv("SYSTEMCORE_HOST", "127.0.0.1")
    yield fake
    fake.stop()


@pytest.fixture
def fake_ssh(monkeypatch):
    """Replace ssh.session with an in-memory session; `holder.session` is the FakeSession tests fill in."""

    class Holder:
        session = FakeSession({})

    @contextlib.asynccontextmanager
    async def opened(host, os_hash=None):
        yield Holder.session

    monkeypatch.setattr(ssh, "session", opened)
    monkeypatch.setenv("SYSTEMCORE_HOST", "127.0.0.1")
    return Holder


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c


class FakeClock:
    """A clock for waiting code: `sleep` moves the time forward at once, so a 10 s wait takes no real time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)  # let other tasks (the in-process server) run


@pytest.fixture
def fast_time(monkeypatch):
    """`fast_time(module)`: give `module` a fake `time.monotonic` and `asyncio.sleep` (nothing else changes, nothing global)."""

    def install(module) -> FakeClock:
        clock = FakeClock()
        real_asyncio = module.asyncio

        class Asyncio:
            sleep = staticmethod(clock.sleep)

            def __getattr__(self, name):
                return getattr(real_asyncio, name)

        monkeypatch.setattr(module, "time", types.SimpleNamespace(monotonic=clock.monotonic))
        monkeypatch.setattr(module, "asyncio", Asyncio())
        return clock

    return install

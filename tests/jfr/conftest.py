"""Fixtures for the profiling server tests: a real JDK when there is one (those tests are skipped otherwise), and a tiny Java program."""

import subprocess
import time

import pytest
from fastmcp import Client
from jfr_helpers import WORK, java_home_tool

from frc_mcp_jfr.spec import SPEC

mcp, apply_toolset = SPEC.load()


@pytest.fixture
def rec_dir(tmp_path, monkeypatch):
    folder = tmp_path / "rec"
    folder.mkdir()
    monkeypatch.setenv("JFR_DIR", str(folder))
    return folder


@pytest.fixture(autouse=True)
def recording_allowed(monkeypatch):
    """Recording is off by default; most tests need it on. The tests about the switch itself delete it."""
    monkeypatch.setenv("JFR_ALLOW_RECORD", "1")


@pytest.fixture
def work_java(tmp_path):
    path = tmp_path / "Work.java"
    path.write_text(WORK, encoding="utf-8")
    return path


@pytest.fixture
def make_recording(rec_dir, work_java):
    """Run the Java program under JFR for a moment and return the recording's file name (a real .jfr from a real JVM)."""
    def make(name="first.jfr", run_ms=2500):
        target = rec_dir / name
        subprocess.run([java_home_tool("java"), f"-XX:StartFlightRecording:filename={target.as_posix()},dumponexit=true,settings=profile",
                        str(work_java), str(run_ms)], capture_output=True, check=True, timeout=120)
        return name
    return make


def _wait_until_attachable(proc, timeout_s=60):
    """Wait until jcmd can attach to the JVM (a slow runner can take much longer than a fixed pause to compile and start).

    Gives up quietly after `timeout_s`: the test then fails with the tool's own error instead of a hang here.
    """
    jcmd = java_home_tool("jcmd")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and proc.poll() is None:
        if subprocess.run([jcmd, str(proc.pid), "VM.uptime"], capture_output=True, check=False, timeout=30).returncode == 0:
            break
        time.sleep(0.5)
    time.sleep(1)  # let the program start its work loop


@pytest.fixture
def running_jvm(work_java):
    """A JVM that stays up for a while, killed at the end of the test."""
    proc = subprocess.Popen([java_home_tool("java"), str(work_java), "60000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait_until_attachable(proc)
    yield proc
    proc.kill()
    proc.wait()


@pytest.fixture
async def client(rec_dir):
    async with Client(mcp) as c:
        yield c


@pytest.fixture(autouse=True)
def full_toolset_after_each_test():
    """The CLI tests apply JFR_TOOLSET=core to the shared server; put every tool back so test order cannot hide jfr_live."""
    yield
    apply_toolset("full")

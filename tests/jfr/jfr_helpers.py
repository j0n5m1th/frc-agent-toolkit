"""Test helpers for the profiling server: find a JDK, skip when there is none, and a tiny Java program to record."""

import os
import re
import shutil
import subprocess

import pytest

WORK = """
public class Work {
    static long sink;
    public static void main(String[] args) throws Exception {
        long end = System.currentTimeMillis() + Long.parseLong(args[0]);
        int rounds = 0;
        while (System.currentTimeMillis() < end) {
            byte[][] junk = new byte[200][];
            for (int i = 0; i < junk.length; i++) junk[i] = new byte[10_000];
            sink += junk.length;
            if (++rounds % 30 == 0) System.gc();
            Thread.sleep(2);
        }
    }
}
"""


def java_home_tool(name):  # kept apart from frc_mcp_jfr.config on purpose: the tests find a JDK without the code under test
    for home in (os.environ.get("JFR_JAVA_HOME"), os.environ.get("JAVA_HOME")):
        if home and (found := shutil.which(name, path=os.path.join(home, "bin"))):
            return found
    return shutil.which(name)


def _jdk_problem():
    r"""Why the Flight Recorder tests cannot run here, or None when they can.

    Besides a JDK 17+ with java, jfr and jcmd, the JDK must be able to START a recording: it needs a writable repository folder (the temp folder).
    Seen on a Windows machine where a tool ran the tests from a restricted environment: `java -XX:StartFlightRecording` exited 1 with "JFR repository
    directory (the Windows Temp folder) exists, but isn't writable", so every test that records failed. That is the machine, not the code, so these tests are skipped there
    (with the reason) and still run everywhere a recording can start, such as CI.
    """
    java = java_home_tool("java")
    if not (java and java_home_tool("jfr") and java_home_tool("jcmd")):
        return "needs a JDK 17+ with java, jfr and jcmd"
    out = subprocess.run([java, "-version"], capture_output=True, text=True, check=False).stderr
    match = re.search(r'version "(\d+)', out)
    if not (match and int(match.group(1)) >= 17):
        return "needs a JDK 17+ with java, jfr and jcmd"
    probe = subprocess.run([java, "-XX:StartFlightRecording", "-version"], capture_output=True, text=True, check=False, timeout=120)
    if probe.returncode != 0:
        detail = next((line for line in (probe.stdout + probe.stderr).splitlines() if "[error]" in line or "Error" in line), "no message")
        return f"this JDK cannot start a Flight Recording here ({detail.strip()[:200]})"
    return None


_PROBLEM = _jdk_problem()
requires_jdk = pytest.mark.skipif(_PROBLEM is not None, reason=_PROBLEM or "")

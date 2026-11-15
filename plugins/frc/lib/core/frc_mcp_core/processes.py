"""Start-up and clean-up rules for child processes (Gradle, CTRE tools), so none is left behind.
Windows: every tracked child joins one Job Object that kills its members when the handle closes, so if the server dies (even by a
crash or kill), Windows ends the children. POSIX: a child starts in its own session (`new_session_kwargs`) so the group can be
killed at once. Standard library only.
"""

import atexit
import contextlib
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)

KILL_TIMEOUT_S = 5.0

_job: Any = None  # the Windows Job Object handle (an int), created on first use; None until then
_job_failed = False
_cleanups: list[Callable[[], None]] = []  # run once on the way out (atexit) and by a termination signal, before the process stops
_children: set[int] = set()  # tracked children that are still running (`remember_child`)


def new_session_kwargs() -> dict[str, Any]:
    """Extra `Popen`/`create_subprocess_exec` keywords: on POSIX the child leads its own process group."""
    return {} if sys.platform == "win32" else {"start_new_session": True}


def _win_job() -> Any:
    """Create (once) the kill-on-close Job Object. Returns its handle, or None when Windows refuses."""
    global _job, _job_failed
    if _job is not None or _job_failed:
        return _job
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class _Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]  # Windows only; mypy checks other platforms too
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        _job_failed = True
        _log.debug("CreateJobObjectW failed: %s", ctypes.get_last_error())  # type: ignore[attr-defined]
        return None
    info = _Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9 = ExtendedLimitInformation
    if not ok:
        _job_failed = True
        _log.debug("SetInformationJobObject failed: %s", ctypes.get_last_error())  # type: ignore[attr-defined]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)
        return None
    _job = handle
    return _job


def track_child(pid: int) -> bool:
    """Make the child die with this server. Windows: assign it to the kill-on-close job; POSIX: nothing to do.

    Returns True when the child was assigned. Never raises: on failure the child is simply not tracked (the caller's
    own clean-up still runs). A grandchild the child started before this call is not in the job.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    try:
        job = _win_job()
        if job is None:
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        proc = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not proc:
            return False
        try:
            return bool(kernel32.AssignProcessToJobObject(job, proc))
        finally:
            kernel32.CloseHandle(proc)
    except (OSError, AttributeError, ValueError) as e:
        _log.debug("could not assign pid %s to the job: %s", pid, e)
        return False


def kill_tree_sync(pid: int, timeout_s: float = KILL_TIMEOUT_S) -> None:
    """End a process and everything it started. Safe for a process that is already gone; never raises."""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, timeout=timeout_s)
        except (subprocess.TimeoutExpired, OSError) as e:
            _log.warning("taskkill for pid %s did not finish: %s", pid, e)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        try:
            leads_group = os.getpgid(pid) == pid
        except ProcessLookupError:
            return
        if leads_group:  # started with new_session_kwargs; never signal our own group by mistake
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)


def register_cleanup(fn: Callable[[], None]) -> None:
    """Run `fn` (it must be quick, idempotent and not raise) when the server exits and when a SIGTERM/SIGHUP ends it."""
    if fn not in _cleanups:
        _cleanups.append(fn)
        atexit.register(fn)


def remember_child(pid: int) -> None:
    """Note a running child so a termination signal kills its tree. Pair with `forget_child` when it has ended."""
    _children.add(pid)


def forget_child(pid: int) -> None:
    _children.discard(pid)


def _kill_remembered() -> None:
    for pid in list(_children):
        kill_tree_sync(pid)


register_cleanup(_kill_remembered)

_HANDLED = ("SIGTERM", "SIGHUP")


def run_cleanups() -> None:
    """Run every registered clean-up now. Never raises."""
    for fn in list(_cleanups):
        try:
            fn()
        except Exception as e:  # noqa: BLE001  one failing clean-up must not stop the rest
            _log.warning("clean-up %s failed: %s", getattr(fn, "__name__", fn), e)


def _exit_on_signal(signum: int, frame: Any) -> None:
    """Kill the children here, then stop at once.

    A plain `SystemExit` is not enough: MCP's stdin reader is a non-daemon thread blocked in `readline`, and Python joins such threads before
    it runs `atexit`, so with stdin still open the process (and its children) would live on. A second signal during the clean-up is ignored.
    """
    for name in _HANDLED:
        other = getattr(signal, name, None)
        if other is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(other, signal.SIG_IGN)
    try:
        run_cleanups()
    finally:
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
        os._exit(128 + signum)


def exit_on_signals() -> list[int]:
    """POSIX: make SIGTERM and SIGHUP end the server promptly with its child processes killed first.

    Python's default for these signals ends the process at once, so children (own process group on POSIX) would keep running; the handler kills them,
    then exits (see `_exit_on_signal`). A signal that already has a handler is left alone. Windows: nothing to do (Python could not run a handler
    for Ctrl+Break while the main thread waits, and the default ends the process, so the Job Object ends the children). Call from the main thread;
    returns the signals taken over.
    """
    if sys.platform == "win32":
        return []
    taken = []
    for name in _HANDLED:
        signum = getattr(signal, name, None)
        try:
            if signum is not None and signal.getsignal(signum) == signal.SIG_DFL:
                signal.signal(signum, _exit_on_signal)
                taken.append(int(signum))
        except (ValueError, OSError) as e:  # not the main thread, or the signal is not allowed here
            _log.debug("could not handle %s: %s", name, e)
    return taken

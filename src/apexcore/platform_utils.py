"""Small operating-system helpers that differ between Windows and POSIX."""

from __future__ import annotations

import contextlib
import os
import platform
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

IS_WINDOWS = os.name == "nt"
T = TypeVar("T")


def describe_platform() -> str:
    """A short human-readable OS description for prompts and diagnostics."""
    system = platform.system() or sys.platform
    if system == "Darwin":
        release = platform.mac_ver()[0] or platform.release()
        return f"macOS {release} ({platform.machine()})"
    if system == "Windows":
        return f"Windows {platform.release()} ({platform.machine()})"
    if system == "Linux":
        name = ""
        with contextlib.suppress(OSError):
            name = platform.freedesktop_os_release().get("PRETTY_NAME", "")
        return f"Linux{' (' + name + ')' if name else ''} {platform.machine()}".strip()
    return f"{system} {platform.release()} ({platform.machine()})"


def is_elevated() -> bool:
    """True if running as root (POSIX) or as an elevated Administrator (Windows)."""
    if IS_WINDOWS:
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def format_argv(argv: list[str]) -> str:
    """Render an argv list the way the current platform's shell would quote it."""
    if IS_WINDOWS:
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _path_exts() -> list[str]:
    if not IS_WINDOWS:
        return [""]
    exts = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
    return [ext.lower() for ext in exts if ext]


def _is_executable(path: Path) -> bool:
    return path.is_file() and (IS_WINDOWS or os.access(path, os.X_OK))


def which_no_cwd(name: str, path_env: str | None = None) -> Path | None:
    """Find ``name`` on PATH, ignoring the current directory.

    :func:`shutil.which` (and ``CreateProcess`` on Windows) search the current
    directory first, so a repository containing ``git.exe`` could hijack a
    command. This lookup only uses absolute PATH entries.
    """
    search = os.environ.get("PATH", "") if path_env is None else path_env
    exts = _path_exts()
    has_ext = IS_WINDOWS and Path(name).suffix.lower() in exts
    for entry in search.split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not Path(entry).is_absolute():
            continue
        base = Path(entry) / name
        candidates = [base] if (has_ext or not IS_WINDOWS) else [Path(f"{base}{e}") for e in exts]
        for candidate in candidates:
            if _is_executable(candidate):
                return candidate
    return None


def call_interruptibly(fn: Callable[[], T], on_cancel: Callable[[], None]) -> T:
    """Run ``fn`` in a worker thread while the main thread waits in short slices.

    On Windows a blocking socket read cannot be interrupted by Ctrl+C, so a slow
    model response would make APEX CORE ignore Ctrl+C until it arrived. Waiting
    here instead keeps Ctrl+C responsive on every platform. On interrupt,
    ``on_cancel`` is called (to abort the request) and ``KeyboardInterrupt``
    is re-raised. Exceptions from ``fn`` are re-raised in the caller.
    """
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # re-raised in the calling thread
            outcome["error"] = exc

    worker = threading.Thread(target=target, name="apex-request", daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(0.1)
    except KeyboardInterrupt:
        on_cancel()
        worker.join(2.0)
        raise
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def popen_group_kwargs() -> dict[str, object]:
    """Start a child in its own process group so it can be killed as a tree,
    and so a terminal Ctrl+C reaches APEX CORE (which then cleans up) rather
    than racing it to the child."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_process_tree(proc: subprocess.Popen[bytes], grace: float = 2.0) -> None:
    """Terminate ``proc`` and its descendants. Safe to call on an exited process."""
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        taskkill = Path(system_root) / "System32" / "taskkill.exe"
        if taskkill.exists():
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    [str(taskkill), "/F", "/T", "/PID", str(proc.pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                )
        with contextlib.suppress(OSError):
            proc.kill()
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        proc.wait(timeout=5)

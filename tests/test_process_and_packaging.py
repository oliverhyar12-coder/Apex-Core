"""Tests that start real processes: the installed entry point, `python -m`, and
interrupting a running `apex ask` with a real signal."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

import pytest

from apexcore import DIST_NAME, __version__
from conftest import write_config
from fakes import FakeOpenAIServer

IS_WINDOWS = os.name == "nt"


def child_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("APEX_")}
    env.update(
        {
            "APEX_CONFIG_DIR": os.environ["APEX_CONFIG_DIR"],
            "APEX_DATA_DIR": os.environ["APEX_DATA_DIR"],
            "APEX_NO_KEYRING": "1",
            "PYTHONIOENCODING": "utf-8",
            **extra,
        }
    )
    return env


def test_python_m_entry_point(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "apexcore", "--version"],
        capture_output=True,
        text=True,
        env=child_env(tmp_path),
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout


def test_console_script_is_registered() -> None:
    try:
        dist = metadata.distribution(DIST_NAME)
    except metadata.PackageNotFoundError:
        pytest.skip("package not installed (run `pip install -e .`)")
    scripts = {ep.name: ep.value for ep in dist.entry_points if ep.group == "console_scripts"}
    assert scripts == {"apex": "apexcore.cli:main"}
    assert dist.version == __version__


def test_installed_apex_command_runs(tmp_path: Path) -> None:
    exe_dir = Path(sys.executable).parent
    candidates = [exe_dir / "apex", exe_dir / "apex.exe", exe_dir / "Scripts" / "apex.exe"]
    apex = next((c for c in candidates if c.exists()), None)
    if apex is None:
        pytest.skip("apex script not installed next to this interpreter")
    result = subprocess.run(
        [str(apex), "doctor"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env(tmp_path),
        timeout=60,
        check=False,
    )
    assert result.returncode == 1  # nothing configured yet
    assert "no default provider" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_errors_never_print_tracebacks(tmp_path: Path) -> None:
    write_config("broken = [")
    result = subprocess.run(
        [sys.executable, "-m", "apexcore", "config", "show"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env(tmp_path),
        timeout=60,
        check=False,
    )
    assert result.returncode == 1
    assert "Could not parse" in result.stderr and "Traceback" not in result.stderr


def test_interrupting_a_request_exits_130(tmp_path: Path) -> None:
    """Send a real Ctrl+C (SIGINT on POSIX, CTRL_BREAK on Windows) while apex
    waits for a provider that never answers."""
    with FakeOpenAIServer([{"hang": True}]) as server:
        write_config(
            'default_provider = "local"\n[providers.local]\n'
            f'base_url = "{server.base_url}"\nmodel = "fake-model"\nmax_retries = 0\n'
            "timeout = 120\n"
        )
        kwargs: dict = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            [sys.executable, "-m", "apexcore", "ask", "hello"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=child_env(tmp_path),
            **kwargs,
        )
        try:
            assert server.request_event.wait(60), "apex never contacted the fake provider"
            time.sleep(0.3)
            try:
                if IS_WINDOWS:
                    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                else:
                    proc.send_signal(signal.SIGINT)
            except OSError as exc:  # e.g. no console attached on some CI runners
                pytest.skip(f"cannot deliver a console signal here: {exc}")
            started = time.monotonic()
            _stdout, stderr = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
    assert time.monotonic() - started < 15
    text = stderr.decode("utf-8", "replace")
    assert proc.returncode == 130, text
    assert "Cancelled" in text and "Traceback" not in text

"""Run a child process safely: argv only, bounded time and output, clean kill."""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from apexcore.platform_utils import kill_process_tree, popen_group_kwargs

_SECRET_ENV = re.compile(
    r"(?i)(API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE_KEY|ACCESS_KEY|SESSION_KEY)"
)
_POLL_INTERVAL = 0.05


def scrubbed_env(
    base: Mapping[str, str] | None = None, extra_names: Iterable[str] = ()
) -> dict[str, str]:
    """A copy of the environment without variables that look like credentials."""
    env = dict(os.environ if base is None else base)
    drop = {name.upper() for name in extra_names}
    return {
        key: value
        for key, value in env.items()
        if key.upper() not in drop and not _SECRET_ENV.search(key)
    }


@dataclass
class ProcessResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration: float
    stdout_truncated: bool
    stderr_truncated: bool


class _CappedReader(threading.Thread):
    """Drain a pipe, keeping at most ``limit`` bytes so a chatty child cannot
    exhaust memory, while still reading everything so the child never blocks."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.chunks: list[bytes] = []
        self.kept = 0
        self.total = 0

    def run(self) -> None:
        try:
            while True:
                chunk = self.stream.read(65536)
                if not chunk:
                    break
                self.total += len(chunk)
                room = self.limit - self.kept
                if room > 0:
                    piece = chunk[:room]
                    self.chunks.append(piece)
                    self.kept += len(piece)
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                self.stream.close()

    def text(self) -> str:
        return decode_output(b"".join(self.chunks))


def decode_output(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Windows tools often write in the console's ANSI/OEM code page.
        import locale

        return data.decode(locale.getpreferredencoding(False) or "utf-8", errors="replace")


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    max_output_bytes: int,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    """Run ``argv`` (no shell) with stdin closed, a timeout, and capped output.

    On timeout, Ctrl+C (``KeyboardInterrupt``) or any other exception, the whole
    process tree is killed before returning or re-raising.
    """
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        **popen_group_kwargs(),  # type: ignore[arg-type]
    )
    assert proc.stdout is not None and proc.stderr is not None
    out_reader = _CappedReader(proc.stdout, max_output_bytes)
    err_reader = _CappedReader(proc.stderr, max_output_bytes)
    out_reader.start()
    err_reader.start()
    timed_out = False
    try:
        deadline = started + timeout
        # Poll rather than block in wait(): a blocking wait cannot be
        # interrupted by Ctrl+C on Windows.
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                kill_process_tree(proc)
                break
            time.sleep(_POLL_INTERVAL)
    except BaseException:
        kill_process_tree(proc)
        raise
    finally:
        out_reader.join(timeout=5)
        err_reader.join(timeout=5)
    return ProcessResult(
        returncode=None if timed_out else proc.returncode,
        stdout=out_reader.text(),
        stderr=err_reader.text(),
        timed_out=timed_out,
        duration=time.monotonic() - started,
        stdout_truncated=out_reader.total > out_reader.kept,
        stderr_truncated=err_reader.total > err_reader.kept,
    )

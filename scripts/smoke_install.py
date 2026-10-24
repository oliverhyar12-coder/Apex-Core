"""Install a built wheel into a fresh virtual environment and exercise `apex`.

Usage:  python scripts/smoke_install.py [DIST_DIR]

Works the same on Linux, macOS, and Windows (no shell features, no network
access beyond installing the wheel's dependencies). Exits non-zero on the first
failed check.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def run(argv: list[str | Path], env: dict[str, str], expect: int = 0) -> str:
    result = subprocess.run(
        [str(a) for a in argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=600,
        check=False,
    )
    output = result.stdout + result.stderr
    shown = " ".join([Path(argv[0]).stem, *(str(a) for a in argv[1:])])
    status = "ok" if result.returncode == expect else "FAILED"
    print(f"[{status}] {shown} -> exit {result.returncode}")
    if result.returncode != expect or "Traceback" in output:
        print(output)
        sys.exit(f"smoke test failed: expected exit {expect}, got {result.returncode}")
    return output


def main() -> None:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        sys.exit(f"expected exactly one wheel in {dist}, found {[w.name for w in wheels]}")
    wheel = wheels[0].resolve()
    print(f"Testing {wheel.name} on {sys.platform} with Python {sys.version.split()[0]}")

    with tempfile.TemporaryDirectory(prefix="apex-smoke-") as tmp_name:
        tmp = Path(tmp_name)
        venv.create(tmp / "venv", with_pip=True)
        bindir = tmp / "venv" / ("Scripts" if os.name == "nt" else "bin")
        python = bindir / ("python.exe" if os.name == "nt" else "python")
        apex = bindir / ("apex.exe" if os.name == "nt" else "apex")

        env = {k: v for k, v in os.environ.items() if not k.startswith("APEX_")}
        env.update(
            APEX_CONFIG_DIR=str(tmp / "config"),
            APEX_DATA_DIR=str(tmp / "data"),
            APEX_NO_KEYRING="1",
            PYTHONIOENCODING="utf-8",
        )
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
             str(wheel)],
            check=True, env=env, timeout=900,
        )  # fmt: skip
        if not apex.exists():
            sys.exit(f"console script not installed at {apex}")

        version = run([apex, "--version"], env)
        assert "apex " in version, version
        run([apex, "--help"], env)
        run([python, "-m", "apexcore", "--version"], env)
        run([apex, "doctor"], env, expect=1)  # nothing configured yet
        run([apex, "init", "--provider", "ollama", "--model", "smoke-test"], env)
        run([apex, "doctor"], env)  # ollama needs no key, so everything passes
        run([apex, "config", "show"], env)
        run([apex, "providers"], env)
        run([apex, "tools"], env)
        run([apex, "history"], env)
        run([apex, "config", "set", "providers.ollama.base_url", "http://127.0.0.1:9/v1"], env)
        run([apex, "config", "set", "providers.ollama.max_retries", "0"], env)
        output = run([apex, "ask", "hello"], env, expect=1)  # nothing listens on port 9
        assert "could not connect" in output, output
    print("Smoke test passed.")


if __name__ == "__main__":
    main()

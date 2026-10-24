"""Shared fixtures.

Every test runs with its own configuration and data directories, no provider
API keys in the environment, no OS keyring, and outbound network access
blocked (only 127.0.0.1 is allowed, for the local fake server). No test can
spend real API credits.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from apexcore import redaction
from apexcore.config import PermissionSettings, ToolSettings
from apexcore.providers import http as http_module
from apexcore.tools.base import ToolContext
from apexcore.tools.workspace import Workspace

KEY_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
)

_real_handle_request = httpx.HTTPTransport.handle_request


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in list(os.environ):
        if name.startswith("APEX_"):
            monkeypatch.delenv(name, raising=False)
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APEX_CONFIG_DIR", str(tmp_path / "apex-config"))
    monkeypatch.setenv("APEX_DATA_DIR", str(tmp_path / "apex-data"))
    monkeypatch.setenv("APEX_NO_KEYRING", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(redaction.GLOBAL_REDACTOR, "_secrets", set())
    yield


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def guarded(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        if request.url.host not in ("127.0.0.1", "localhost"):
            raise RuntimeError(f"Tests must not access the network: {request.url}")
        return _real_handle_request(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded)


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], list[httpx.Request]]:
    """Route every adapter request to ``handler``; returns the list of requests seen."""

    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def recording(request: httpx.Request) -> httpx.Response:
            request.read()
            seen.append(request)
            return handler(request)

        def make_client(*, base_url: str, headers: dict[str, str], timeout: float) -> httpx.Client:
            return httpx.Client(
                base_url=base_url,
                headers={"User-Agent": http_module.USER_AGENT, **headers},
                transport=httpx.MockTransport(recording),
                timeout=timeout,
            )

        monkeypatch.setattr(http_module, "make_client", make_client)
        return seen

    return install


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    root = tmp_path / "workspace"
    root.mkdir()
    return Workspace(root)


@pytest.fixture
def ctx(ws: Workspace) -> ToolContext:
    return ToolContext(
        workspace=ws,
        tool_settings=ToolSettings(),
        permission_settings=PermissionSettings(),
    )


@pytest.fixture
def not_elevated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend we are not root/Administrator (CI containers often are)."""
    monkeypatch.setattr("apexcore.tools.execute.is_elevated", lambda: False)


def can_symlink(tmp_path: Path) -> bool:
    target = tmp_path / "symlink-probe-target"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "symlink-probe-link"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    link.unlink()
    target.unlink()
    return True


@pytest.fixture
def symlinks(tmp_path: Path) -> None:
    if not can_symlink(tmp_path):
        pytest.skip("symlinks are not permitted here (Windows without Developer Mode?)")


def write_config(text: str) -> Path:
    path = Path(os.environ["APEX_CONFIG_DIR"]) / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path

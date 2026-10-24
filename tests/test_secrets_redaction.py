from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from apexcore import paths, secrets
from apexcore.redaction import REDACTED, Redactor
from apexcore.secrets import lookup_api_key, store_in_env_file


def test_env_var_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    store_in_env_file("MY_KEY", "from-file-123456")
    monkeypatch.setenv("MY_KEY", "from-env-123456")
    lookup = lookup_api_key("p", "MY_KEY")
    assert (lookup.value, lookup.source) == ("from-env-123456", "env")


def test_credentials_file_fallback_and_privacy() -> None:
    store_in_env_file("MY_KEY", "from-file-123456")
    lookup = lookup_api_key("p", "MY_KEY")
    assert (lookup.value, lookup.source) == ("from-file-123456", "file")
    assert "MY_KEY" not in os.environ  # never injected into the process environment
    if os.name == "posix":
        assert paths.env_file().stat().st_mode & 0o777 == 0o600


def test_missing_key() -> None:
    lookup = lookup_api_key("p", "NOT_SET_ANYWHERE")
    assert not lookup.found and lookup.source is None


def test_cwd_env_file_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_text("MY_KEY=planted-123456\n", encoding="utf-8")
    monkeypatch.chdir(project)
    assert not lookup_api_key("p", "MY_KEY").found


def test_keyring_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[tuple[str, str], str] = {("apex-core", "p"): "from-keyring-123"}
    fake = types.SimpleNamespace(
        get_password=lambda service, user: store.get((service, user)),
        set_password=lambda service, user, value: store.__setitem__((service, user), value),
        delete_password=lambda service, user: store.pop((service, user)),
        get_keyring=lambda: types.SimpleNamespace(),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    # APEX_NO_KEYRING=1 (set for all tests) disables the keyring...
    assert not lookup_api_key("p", "UNSET_VAR").found
    monkeypatch.delenv("APEX_NO_KEYRING")
    lookup = lookup_api_key("p", "UNSET_VAR")
    assert (lookup.value, lookup.source) == ("from-keyring-123", "keyring")
    secrets.store_in_keyring("q", "another-key-123")
    assert store[("apex-core", "q")] == "another-key-123"
    assert secrets.delete_from_keyring("q") is True


def test_known_secret_redaction() -> None:
    r = Redactor(["my-very-secret-value"])
    assert r.redact("token=my-very-secret-value!") == f"token={REDACTED}!"
    r.add("short")  # too short to redact safely
    assert r.redact("short") == "short"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 40,
        "xoxb-1234567890-abcdefghij",
        "AIza" + "c" * 35,
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_token_formats_are_redacted(secret: str) -> None:
    text = f"before {secret} after"
    redacted = Redactor().redact(text)
    assert secret not in redacted
    assert redacted.startswith("before ") and redacted.endswith(" after")


def test_ordinary_code_is_not_mangled() -> None:
    code = 'password = request.form["password"]\ntoken = get_token()\nsk = 1\n'
    assert Redactor().redact(code) == code


def test_aggressive_redaction() -> None:
    r = Redactor()
    assert "abc123def456ghi" not in r.redact_aggressive("Authorization: Bearer abc123def456ghi")
    assert "hunter2hunter2" not in r.redact_aggressive('{"api_key": "hunter2hunter2"}')
    assert "s3cr3tvalue" not in r.redact_aggressive("x-api-key=s3cr3tvalue")

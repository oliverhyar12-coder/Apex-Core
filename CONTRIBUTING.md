# Contributing to APEX CORE

Thanks for helping. This guide covers setting up a development environment on Linux,
macOS, and Windows, running the checks, and the rules for changes.

## Setup

You need Python 3.11+ and git.

**Linux / macOS (bash, zsh):**

```bash
git clone https://github.com/oliverhyar12-coder/Apex-Core.git
cd Apex-Core
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
apex --version
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/oliverhyar12-coder/Apex-Core.git
cd Apex-Core
py -m venv .venv
.\.venv\Scripts\Activate.ps1   # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
apex --version
```

For fish, use `source .venv/bin/activate.fish`; for Command Prompt,
`.venv\Scripts\activate.bat`.

Tip: while developing, point APEX CORE at throwaway directories so you don't touch your
real configuration:

```bash
export APEX_CONFIG_DIR="$PWD/.dev/config" APEX_DATA_DIR="$PWD/.dev/data"   # bash/zsh
```

```powershell
$env:APEX_CONFIG_DIR = "$PWD\.dev\config"; $env:APEX_DATA_DIR = "$PWD\.dev\data"
```

## Checks

These are the same commands CI runs:

```bash
python -m pytest                # the full test suite (about 20 seconds)
ruff check .                    # lint
ruff format --check .           # formatting (run `ruff format .` to fix)
python -m build                 # sdist and wheel into dist/
python scripts/smoke_install.py dist   # install the wheel into a fresh venv and run apex
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the tests on Ubuntu,
macOS (Apple Silicon and Intel), and Windows with Python 3.11 to 3.14. It also runs the
tests against the lowest supported dependency versions, and builds and smoke-tests the
package on all three operating systems.

## Rules for tests

- **No network, no real keys.** `tests/conftest.py` blocks every HTTP request except
  to `127.0.0.1`, removes provider key variables, disables the keyring, and gives each
  test private configuration and data directories. Use the `mock_http` fixture
  (`httpx.MockTransport`) or `tests/fakes.py` (`ScriptedProvider`,
  `FakeOpenAIServer`). Never add a test that needs a paid API.
- **Cross-platform.** Use `pathlib`, `sys.executable` (never `python3`, `bash`, `echo`,
  or `ls`) for subprocesses, and pass `encoding="utf-8"` when opening text files (ruff
  enforces this). If something genuinely only applies to one OS, skip it with a reason,
  for example `@pytest.mark.skipif(os.name != "nt", reason="...")`. Never skip a test
  just because it fails on one platform; fix it.
- Don't weaken or delete a test to make CI pass.

## Project layout

```text
src/apexcore/
  cli/            Typer commands, terminal UI, chat REPL, session assembly
  agent/          bounded agent loop and system prompt
  providers/      provider interface, HTTP plumbing, OpenAI-compatible and Anthropic adapters, presets
  tools/          workspace containment and the tools (files, git, edit, run_command)
  config.py       settings model (Pydantic Settings + TOML) and editing
  permissions.py  permission policy and approval types
  history.py      SQLite history store
  secrets.py      API-key lookup (env, credentials file, keyring)
  redaction.py    secret redaction
  paths.py        per-OS directories
  platform_utils.py  OS differences: elevation, PATH lookup, process-tree kill, interrupts
tests/            pytest suite (see conftest.py)
scripts/          packaging smoke test
```

## Adding a provider

- If it speaks the OpenAI Chat Completions format, add a `ProviderPreset` in
  `providers/presets.py`; that is usually all it takes. Only suggest a default model if
  its name is a long-lived alias.
- Otherwise, write an adapter implementing `providers.base.Provider` (see
  `providers/anthropic.py`). Reuse `HTTPProviderMixin` for retries, error mapping, and
  cancellation; never put keys in URLs or error messages; and add MockTransport tests
  for text, tool calls, streaming, and each error class.

## Adding a tool

Tools are the security boundary, so new tools need extra care:

1. Give it the right `Risk` (READ, WRITE, or EXECUTE). Anything that changes state is
   not READ.
2. Validate arguments with a Pydantic model and resolve **every** path with
   `Workspace.resolve`. Call `check_not_secret` for file contents.
3. Do the checks in `prepare()` and build a `PreparedAction` whose `summary` and
   `details` show the user exactly what will happen. Perform side effects only in
   `run`.
4. Redact output with `ctx.redactor.redact` and keep it bounded.
5. Add tests for traversal, symlinks, secrets, and the denial path, and update
   SECURITY.md.

## Pull requests

- Keep changes focused, and update `CHANGELOG.md` under "Unreleased".
- Update the README or SECURITY.md when behaviour, commands, or security properties
  change.
- Never commit secrets. `.env` files are git-ignored; `.env.example` holds placeholders
  only.
- By contributing you agree that your contribution is licensed under the MIT License.

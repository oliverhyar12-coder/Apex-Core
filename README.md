# APEX CORE

APEX CORE is an open-source AI assistant and coding agent that runs entirely in your
terminal. You bring your own API key (or a local model), and the `apex` command gives
you chat, one-shot questions, and agent tasks that can read your project, propose edits
as diffs, and run commands only after you approve them.

- **Terminal only.** No GUI, web dashboard, background service, or telemetry.
- **Bring your own provider.** OpenAI, Anthropic, DeepSeek, Google Gemini, Mistral,
  OpenRouter, Groq, Ollama, LM Studio, or any OpenAI-compatible endpoint.
- **Safe by default.** Reading is limited to the workspace; every edit and command asks
  first; credentials files and `.git` are off-limits; commands run without a shell.
- **Cross-platform.** Linux (including Arch and CachyOS), macOS (Apple Silicon and
  Intel), and Windows 10/11 (PowerShell, Windows Terminal, Command Prompt).

> **Status: 0.1.0rc1, a release candidate.** It is not published on PyPI yet;
> install it from GitHub as shown below. See [Platform status](#platform-status) for what
> has been verified where, and [Limitations](#limitations) for what is not done yet.

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [Providers and API keys](#providers-and-api-keys)
- [Using apex](#using-apex)
- [Configuration](#configuration)
- [Security model](#security-model)
- [API costs](#api-costs)
- [Privacy](#privacy)
- [Troubleshooting](#troubleshooting)
- [Platform status](#platform-status)
- [Limitations](#limitations)
- [Development](#development)

## Install

You need **Python 3.11 or newer**. `git` is optional; it is used for the `git_status`
and `git_diff` tools. The recommended installer is [pipx](https://pipx.pypa.io), which
puts `apex` in its own isolated environment.

> **Important:** the package named `apex-core` on PyPI is an **unrelated project**.
> Do not run `pip install apex-core`. This project's distribution name is
> `apex-core-cli`, and it has not been published to PyPI yet.

### Linux

```bash
# Arch Linux / CachyOS
sudo pacman -S python-pipx
# Debian / Ubuntu
sudo apt install pipx
# Fedora
sudo dnf install pipx

pipx ensurepath          # then open a new terminal
pipx install "git+https://github.com/oliverhyar12-coder/Apex-Core.git"
apex --version
```

### macOS

```bash
# With Homebrew (optional):  brew install pipx
# Without Homebrew:
python3 -m pip install --user pipx
python3 -m pipx ensurepath   # then open a new terminal

pipx install "git+https://github.com/oliverhyar12-coder/Apex-Core.git"
apex --version
```

### Windows (PowerShell)

Install Python 3.11+ from [python.org](https://www.python.org/downloads/windows/) or
the Microsoft Store, and `git` from [git-scm.com](https://git-scm.com/download/win)
(needed to install from GitHub). Then:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath        # then open a new terminal
pipx install "git+https://github.com/oliverhyar12-coder/Apex-Core.git"
apex --version
```

WSL is not required. Windows Terminal is recommended for the best colours and
Unicode support, but plain PowerShell and Command Prompt work too.

### Optional: OS keyring support

To store API keys in the macOS Keychain, Windows Credential Locker, or the Linux
Secret Service instead of environment variables:

```bash
pipx inject apex-core-cli keyring
```

### Without pipx

```bash
python3 -m venv ~/.venvs/apex          # Windows: py -m venv $HOME\venvs\apex
~/.venvs/apex/bin/pip install "git+https://github.com/oliverhyar12-coder/Apex-Core.git"
~/.venvs/apex/bin/apex --version       # Windows: $HOME\venvs\apex\Scripts\apex.exe
```

### Upgrade and uninstall

```bash
pipx upgrade apex-core-cli      # or: pipx install --force "git+https://..."
pipx uninstall apex-core-cli
```

Uninstalling leaves your configuration and history in place; see
[Configuration](#configuration) for where they are, or run `apex history clear` first.

## Quick start

```bash
apex init        # pick a provider and model (interactive)
```

`init` tells you which environment variable needs your API key. Set it in your shell
(see [Providers and API keys](#providers-and-api-keys)), then:

```bash
apex doctor      # check everything; add --online to test the connection
apex chat        # interactive chat in the current directory
```

Non-interactive setup, for example for scripts:

```bash
apex init --provider openai --model <model-id>
apex init --provider ollama --model llama3.2
apex init --provider custom --name myserver --base-url http://localhost:8000/v1 --api-key-env ""
```

## Providers and API keys

| Preset       | Adapter                | Base URL                                                  | Key variable         |
|--------------|------------------------|-----------------------------------------------------------|----------------------|
| `openai`     | OpenAI-compatible      | `https://api.openai.com/v1`                               | `OPENAI_API_KEY`     |
| `anthropic`  | Anthropic Messages API | `https://api.anthropic.com/v1`                            | `ANTHROPIC_API_KEY`  |
| `deepseek`   | OpenAI-compatible      | `https://api.deepseek.com`                                | `DEEPSEEK_API_KEY`   |
| `gemini`     | OpenAI-compatible      | `https://generativelanguage.googleapis.com/v1beta/openai` | `GEMINI_API_KEY`     |
| `mistral`    | OpenAI-compatible      | `https://api.mistral.ai/v1`                               | `MISTRAL_API_KEY`    |
| `openrouter` | OpenAI-compatible      | `https://openrouter.ai/api/v1`                            | `OPENROUTER_API_KEY` |
| `groq`       | OpenAI-compatible      | `https://api.groq.com/openai/v1`                          | `GROQ_API_KEY`       |
| `ollama`     | OpenAI-compatible      | `http://localhost:11434/v1`                               | none (local)         |
| `lmstudio`   | OpenAI-compatible      | `http://localhost:1234/v1`                                | none (local)         |

Gemini is reached through Google's OpenAI-compatibility endpoint; there is no native
Gemini adapter yet. Anthropic uses a native adapter. `apex models` lists the models your
key can use, and `apex providers` shows which providers are configured and whether their
keys are present.

Add any other OpenAI-compatible server (vLLM, llama.cpp server, LiteLLM, a corporate
gateway...):

```bash
apex providers add mygateway --base-url https://llm.example.com/v1 \
    --api-key-env MYGATEWAY_KEY --model some-model --default
```

### Setting your API key

APEX CORE never stores keys in `config.toml`. It looks for a key in this order:

1. **An environment variable** (the name `apex init` showed, e.g. `OPENAI_API_KEY`):

   | Shell                  | This session                       | Permanently                                                         |
   |------------------------|------------------------------------|---------------------------------------------------------------------|
   | bash / zsh             | `export OPENAI_API_KEY="sk-..."`   | add that line to `~/.bashrc` or `~/.zshrc`                          |
   | fish                   | `set -x OPENAI_API_KEY "sk-..."`   | `set -Ux OPENAI_API_KEY "sk-..."`                                   |
   | PowerShell             | `$env:OPENAI_API_KEY = "sk-..."`   | `[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "sk-...", "User")` |
   | Command Prompt         | `set OPENAI_API_KEY=sk-...`        | `setx OPENAI_API_KEY sk-...` (then open a new window)               |

2. **The private credentials file** in your configuration directory
   (`apex config path` shows it): `apex config set-key openai --env-file`.
   On Linux and macOS the file is created readable only by you.
3. **The OS keyring**, if the optional `keyring` package is installed:
   `apex config set-key openai`.

APEX CORE deliberately **ignores `.env` files in your working directory**: a repository
you clone must not be able to change where your API traffic goes.

## Using apex

| Command | What it does |
|---|---|
| `apex init` | Choose a provider and model; create the config and history database. |
| `apex doctor [--online]` | Check Python, paths, config, API key, history, git, and terminal; `--online` also contacts the provider. |
| `apex config [show\|path\|get\|set\|unset\|set-key\|delete-key]` | View and change settings and stored keys. |
| `apex providers [list\|add\|remove\|use]` | Manage providers. |
| `apex models [-p PROVIDER]` | List models offered by a provider. |
| `apex chat` | Interactive chat with workspace tools. |
| `apex ask "question"` | One question; the answer goes to stdout, status to stderr. |
| `apex task "description"` | Let the agent work on a task, approving each edit and command. |
| `apex tools` | List tools and whether each runs automatically or asks first. |
| `apex history [list\|show\|delete\|clear]` | View and delete local history. |

Common options: `--provider/-p`, `--model/-m`, `--workspace/-w DIR` (default: the current
directory), `--read-only`, and `--no-history`. Run `apex <command> --help` for the rest.

### Chat

```text
$ apex chat
you> why does the test in tests/test_api.py fail?
  -> read tests/test_api.py
     ok
  ...
```

Inside a chat:

| Input | Effect |
|---|---|
| `/help` | List chat commands. |
| `/model [name]` | Show the model, or switch models for this session. |
| `/clear` | Start a new conversation. |
| `/tools` | Show the tools in this session. |
| `/usage` | Show token usage (and estimated cost, if configured). |
| `/exit`, `/quit` | Leave. Ctrl+D (Ctrl+Z then Enter on Windows) also works. |
| Ctrl+C | While the model is working: cancel that turn. At the prompt: leave. |

`apex chat --resume <id>` continues a saved chat, `--no-tools` turns tools off, and
`--read-only` allows only reading.

### Ask

```bash
apex ask "Explain the difference between a process and a thread"
git diff | apex ask -                     # read the question from stdin
apex ask --tools "What does src/main.py do?"   # allow read-only file tools
```

The answer is the only thing written to stdout, so `apex ask` works in pipes. `ask`
never edits files or runs commands.

### Task

```bash
apex task "Add a --verbose flag to cli.py and update the README"
apex task --read-only "Review this repository for obvious bugs"
apex task --max-steps 25 "Make the failing tests pass"
```

The agent reads files freely inside the workspace. Before every edit it shows a diff,
and before every command it shows the exact program and arguments. Answer `y`
(yes), `n` (no), `a` (for the rest of the session: allow all edits, or allow this exact
command again; never offered for flagged commands), or type a reason to refuse with
feedback to the model. At the end you get a table of every action
and its result.

Without an interactive terminal (in a pipe or a CI job), anything that needs approval
is refused, and those tools are not offered to the model.

### Exit codes

`0` success · `1` error (configuration, provider, network...) · `2` usage error ·
`130` cancelled with Ctrl+C.

## Configuration

Settings live in `config.toml`, history in `history.sqlite3`:

| OS      | Configuration                                         | History                                         |
|---------|-------------------------------------------------------|-------------------------------------------------|
| Linux   | `~/.config/apex-core/` (or `$XDG_CONFIG_HOME`)        | `~/.local/share/apex-core/` (or `$XDG_DATA_HOME`) |
| macOS   | `~/Library/Application Support/apex-core/`           | same                                            |
| Windows | `%LOCALAPPDATA%\apex-core\`                           | same                                            |

Set `APEX_CONFIG_DIR` and `APEX_DATA_DIR` to use other directories; `apex config path`
prints what is in effect.

Example `config.toml`:

```toml
default_provider = "openai"

[providers.openai]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
model = "your-model-id"
timeout = 60            # seconds
max_retries = 2         # retries for rate limits, 5xx errors, and network errors
stream = true           # set false for servers with broken streaming
supports_tools = true   # set false for models without tool calling
# temperature = 0.2
# max_output_tokens = 4096

[providers.openai.pricing]   # optional: enables cost estimates
input_per_million = 0.0      # your price per 1M input tokens
output_per_million = 0.0     # your price per 1M output tokens
currency = "USD"

[agent]
max_steps = 12          # model calls per turn

[permissions]
write = "ask"           # ask | allow | deny
execute = "ask"         # ask | deny
auto_approve_commands = [["git", "status"], ["python", "-m", "pytest"]]
allow_elevated = false  # never run commands while apex runs as root/Administrator

[tools]
command_timeout = 120
max_output_chars = 20000
extra_secret_patterns = ["*.secret"]
extra_ignore_dirs = ["vendor"]

[history]
enabled = true
```

Use `apex config set` rather than editing by hand if you like (values are validated
before saving):

```bash
apex config set agent.max_steps 20
apex config set providers.openai.model your-model-id
apex config set permissions.auto_approve_commands '[["python", "-m", "pytest"]]'
```

Every setting can also come from the environment as `APEX_<SECTION>__<KEY>`, for example
`APEX_AGENT__MAX_STEPS=20`. `APEX_PROVIDER` and `APEX_MODEL` pick the provider and model
for one shell session. Command-line options override everything.

## Security model

The short version (details and limits in [SECURITY.md](SECURITY.md)):

- **Workspace containment.** Tools only touch paths inside the workspace, after
  resolving `..` and symlinks. The `.git` directory and credentials files (`.env`,
  `*.pem`, `id_rsa`, `.npmrc`, ...) are blocked.
- **Approval.** Edits show a diff and commands show the exact argv before anything
  happens. Destructive or opaque commands (`rm`, `git reset --hard`, `bash -c`,
  `python -c`, ...) are flagged and can never be auto-approved. Privilege escalation
  (`sudo`, `runas`...), disk formatting, and shutdown commands are always refused.
- **No shell.** Commands run from an argument list, with stdin closed, a timeout, an
  output cap, a working directory inside the workspace, and credential-like environment
  variables (`*_API_KEY`, `*TOKEN*`, `*SECRET*`...) removed.
- **Not as root.** If apex runs as root or an elevated Administrator, command execution
  is disabled.
- **Secrets.** Keys stay out of config files, logs, history, and error messages; known
  token formats are redacted from tool output before it reaches the model.
- **Untrusted content.** File contents and command output are treated as data. The
  model is told so, but **prompt injection is not solved**: a malicious file can still
  persuade the model to *propose* harmful actions. Your approval is the safeguard, so
  read what you approve.

**This is not a sandbox.** An approved command runs with your full user permissions, and
running a project's tests runs that project's code. For untrusted repositories, use
`--read-only`, or run apex inside a container or VM.

## API costs

- Every model call is billed by your provider. In `task` and `chat`, each tool step is
  another call that resends the conversation so far, so long tasks cost more.
  `agent.max_steps` (default 12) caps the calls per turn.
- Token counts are shown after `ask` and `task`, and by `/usage` in chat, whenever the
  provider reports them.
- APEX CORE ships no price list, because prices change. Add `pricing` to a provider
  (see [Configuration](#configuration)) to see an estimate. Treat it as an estimate;
  your provider's billing page is authoritative.
- `apex models` and `apex doctor --online` make a model-listing request, which providers
  normally do not charge for.
- Ollama and LM Studio run on your machine and have no per-token cost.
- The automated tests never contact a paid API.

## Privacy

- No telemetry, analytics, update checks, or background processes.
- Conversations and task logs are stored only in your local SQLite file.
  `apex history delete <id>` and `apex history clear` remove them; deleted data is
  overwritten (`secure_delete`), and `clear` also compacts the file. Set
  `history.enabled = false` or pass `--no-history` to store nothing.
- What leaves your machine: your messages, plus any file contents and command output
  the agent reads, are sent to the provider you configured. With a local provider
  (Ollama, LM Studio) nothing leaves your machine.
- The history file is not encrypted. On Linux and macOS it is readable only by you.

## Troubleshooting

| Problem | What to do |
|---|---|
| `apex: command not found` | Run `pipx ensurepath` and open a new terminal. On Windows, check that `%USERPROFILE%\.local\bin` is on `PATH`. |
| `No API key found` | Set the variable `apex doctor` names, in the same shell you run apex from. On Windows, open a new window after `setx`. |
| `authentication failed (HTTP 401)` | The key is wrong or revoked. `apex doctor` shows where it was loaded from. |
| `model not found` | `apex models` lists what your key can use; then `apex config set providers.<name>.model <id>`. |
| `rate limited` / `quota or credit exhausted` | apex retries rate limits automatically; quota errors mean checking the provider's billing settings. |
| `could not connect` | Check your network. For Ollama or LM Studio, make sure the server is running. Behind a proxy, set `HTTPS_PROXY`; for a custom CA, set `SSL_CERT_FILE`. |
| `request rejected` mentioning tools | The model does not support tool calling: use `--no-tools`, or `apex config set providers.<name>.supports_tools false`. |
| Garbled or duplicated streaming output | `apex config set providers.<name>.stream false`. |
| Odd characters on Windows | Use Windows Terminal, or run `chcp 65001` in Command Prompt. |
| Keyring prompts or hangs (headless Linux) | Set `APEX_NO_KEYRING=1` and use an environment variable or `--env-file`. |
| "command execution is disabled" | apex is running as root or an elevated Administrator. Run it as your normal user. |
| Edits or commands are always refused | You are not in an interactive terminal, `--read-only` is set, or `permissions.write/execute = "deny"`. |

## Platform status

CI runs the full test suite on every push (see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)):

| Platform | Python | How it is verified |
|---|---|---|
| Linux x86_64 (Ubuntu runner) | 3.11, 3.12, 3.13, 3.14 | CI tests, lowest-dependency tests, package build, and clean-install smoke test |
| macOS arm64 / Apple Silicon (`macos-latest`) | 3.11, 3.12, 3.13, 3.14 | CI tests, package build, and clean-install smoke test |
| macOS x86_64 / Intel (`macos-15-intel`) | 3.13 | CI tests |
| Windows x86_64 (`windows-latest`) | 3.11, 3.12, 3.13, 3.14 | CI tests, package build, and clean-install smoke test (PowerShell) |
| Arch Linux / CachyOS | 3.11+ | Expected to work like any Linux (pure Python, no distro-specific code); not run in CI. |

Last full verification, 2026-10-24: all 18 CI jobs passed on macOS 26.6 (arm64),
macOS 15.7 (Intel), Windows Server 2025 (build 26100, default cp1252 encoding), and
Ubuntu (Linux 7.0, glibc 2.43). That covers 282 to 286 tests per job; the skipped tests
apply only to the other operating systems. On every platform this included a real
Ctrl+C (Ctrl+Break on Windows) interrupting a hung request, process-tree kills,
symlink-escape checks, and the git fsmonitor protection.

Results for the current commit are on the repository's
[Actions page](https://github.com/oliverhyar12-coder/Apex-Core/actions). Not covered by
CI: Windows on ARM, Linux on ARM, Windows 10 specifically (runners use Windows Server),
and interactive use in each terminal emulator. Provider adapters are tested against
mocked and local fake servers, not against the live services.

## Limitations

- Not yet published to PyPI; install from GitHub.
- Provider adapters are verified against mocked responses and a local fake server, not
  against live services. Real services can differ in details.
- No native Gemini or Ollama adapters (both are used through their OpenAI-compatible
  endpoints), no image or file attachments, and no extended "thinking" output.
- Tools: no file deletion or rename tool, no web access, no multi-file patch tool.
- Command execution is approval-gated but **not sandboxed** (see SECURITY.md).
- Chat input is single-line; Markdown in answers is shown as plain text.
- Token budgets per task and context-window trimming for very long chats are not
  implemented yet; `agent.max_steps` is the main cost control.
- The history database is not encrypted.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup on Linux, macOS, and Windows, plus
the test and lint commands. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) © 2026 Apex Team

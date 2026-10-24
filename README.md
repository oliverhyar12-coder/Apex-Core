# APEX CORE

APEX CORE is an open-source AI assistant and coding agent that runs entirely in your
terminal. Run `apex` in a project folder and chat with an agent that reads your code,
keeps a plan, remembers your preferences, proposes edits as diffs, and runs commands
only after you approve them. You bring your own API key, or use a local model.

- **Terminal only.** No GUI, web dashboard, background service, or telemetry.
- **Bring your own provider.** OpenAI, Anthropic, DeepSeek, Google Gemini, Mistral,
  OpenRouter, Groq, Ollama, LM Studio, or any OpenAI-compatible endpoint.
- **A proper chat.** A Hermes-style start screen with a blue tiger, streaming Markdown
  answers, a live feed of what the agent is doing, slash commands with Tab completion,
  `@file` attachments, multi-line input, a status bar, saved conversations you can
  resume, and long-term memory. The design is inspired by Nous Research's
  [Hermes Agent](https://github.com/NousResearch/hermes-agent) (this project is not
  affiliated with it).
- **Safe by default.** Reading is limited to the workspace; every edit and command asks
  first (once, for the session, or always); credentials files and `.git` are off-limits;
  commands run without a shell.
- **Cross-platform.** Linux (including Arch and CachyOS), macOS (Apple Silicon and
  Intel), and Windows 10/11 (PowerShell, Windows Terminal, Command Prompt).

> **Status: 0.2.0rc1, a release candidate.** It is not published on PyPI; install it
> from GitHub as shown below. See [Platform status](#platform-status) for what has been
> verified where, and [Limitations](#limitations) for what is not done yet.

**New here? Read the [usage guide](docs/USAGE.md)**: first run, chatting, approvals,
slash commands, memory, and sessions, with examples.

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

You need **Python 3.11 or newer**. `git` is used to download the code and by the
`git_status` and `git_diff` tools.

> **Important:** the package named `apex-core` on PyPI is an **unrelated project**.
> Do not run `pip install apex-core`. This project's distribution name is
> `apex-core-cli`, and it has not been published to PyPI.

### Linux and macOS (including Arch and CachyOS)

```bash
git clone https://github.com/oliverhyar12-coder/Apex-Core.git
cd Apex-Core
bash scripts/install.sh
```

Or without cloning:

```bash
curl -fsSL https://raw.githubusercontent.com/oliverhyar12-coder/Apex-Core/main/scripts/install.sh | bash
```

Then **open a new terminal** and run `apex`.

The installer finds Python 3.11+, creates a private virtual environment in
`~/.local/share/apex-core/venv`, links `apex` into `~/.local/bin`, and adds that folder
to your PATH in bash, zsh, fish, or `~/.profile`. It does not need pipx and never uses
sudo. If Python is missing it prints the command for your system, for example
`sudo pacman -S python` on Arch and CachyOS. Options: `--with-keyring`, `--ref BRANCH`,
`--source PATH`, `--uninstall`. Environment variables: `APEX_INSTALL_DIR`,
`APEX_BIN_DIR`, and `APEX_NO_MODIFY_PATH=1`.

### Windows (PowerShell)

Install Python 3.11+ from [python.org](https://www.python.org/downloads/windows/) (tick
"Add python.exe to PATH") and [git](https://git-scm.com/download/win). Then:

```powershell
git clone https://github.com/oliverhyar12-coder/Apex-Core.git
cd Apex-Core
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Or without cloning:

```powershell
irm https://raw.githubusercontent.com/oliverhyar12-coder/Apex-Core/main/scripts/install.ps1 | iex
```

It installs into `%LOCALAPPDATA%\apex-core` and adds `%LOCALAPPDATA%\apex-core\bin` to
your user PATH. Open a new window and run `apex`. Options: `-WithKeyring`, `-Ref BRANCH`,
`-Source PATH`, `-Uninstall`.

WSL is not required. Windows Terminal is recommended for the best colours and Unicode
support, but plain PowerShell and Command Prompt work too.

### Update and uninstall

Run the installer again to update. To remove it, run `bash scripts/install.sh --uninstall`
(Windows: `scripts\install.ps1 -Uninstall`). Your configuration, memory, and history are
kept; see [Configuration](#configuration) for where they are, or run
`apex history clear` and `apex memory clear` first.

### With pipx or pip instead

```bash
pipx install "git+https://github.com/oliverhyar12-coder/Apex-Core.git"
# or, in a virtual environment of your own:
python3 -m venv ~/.venvs/apex && ~/.venvs/apex/bin/pip install "git+https://github.com/oliverhyar12-coder/Apex-Core.git"
```

Add keyring support with `pipx inject apex-core-cli keyring`. If pipx fails with
`ModuleNotFoundError: No module named 'filelock'` (seen on CachyOS), the problem is in
the system's pipx, not APEX CORE: run `sudo pacman -S python-filelock`, or use the
installer script above.

## Quick start

```bash
cd ~/my-project
apex
```

The first run walks you through setup: choose a provider, paste your API key (hidden;
stored only on this computer, never in the config file), and APEX CORE checks the key
and lists the models it can use so you can pick one. Then the chat opens:

![The APEX CORE chat: a blue block-letter title, the tiger logo, the tools and commands
panel, a question with an attached file, and the status bar](docs/images/apex-chat.png)

Like Hermes Agent, the start screen shows the tools, the commands, and your session; the
status bar under the prompt shows the model, how full the context is, the time of the
last answer, and the folder. Type `@` and a file name to attach a file to your message.

When it wants to run the tests, it asks first (see [Approvals](#approvals)), then shows
the end of the output:

```text
┊ 💻 run python -m pytest tests/test_api.py -q  ✗ 2.1s
┊    FAILED tests/test_api.py::test_login - AssertionError
┊    1 failed, 4 passed in 0.31s
┊    exit code 1
```

Type a message and press Enter. `/help` lists the commands and Ctrl+D exits. Next
time, `apex -c` continues where you left off. No API key? Choose `ollama` or `lmstudio`
to use a model running on your own machine.

To set up without prompts, for example in scripts:

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
gateway...) in `apex setup`, or with:

```bash
apex providers add mygateway --base-url https://llm.example.com/v1 \
    --api-key-env MYGATEWAY_KEY --model some-model --default
```

### Setting your API key

The easiest way is to paste it into `apex setup` (or the first-run setup). APEX CORE
never stores keys in `config.toml`. It looks for a key in this order:

1. **An environment variable** (the name `apex doctor` shows, e.g. `OPENAI_API_KEY`):

   | Shell                  | This session                       | Permanently                                                         |
   |------------------------|------------------------------------|---------------------------------------------------------------------|
   | bash / zsh             | `export OPENAI_API_KEY="sk-..."`   | add that line to `~/.bashrc` or `~/.zshrc`                          |
   | fish                   | `set -x OPENAI_API_KEY "sk-..."`   | `set -Ux OPENAI_API_KEY "sk-..."`                                   |
   | PowerShell             | `$env:OPENAI_API_KEY = "sk-..."`   | `[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "sk-...", "User")` |
   | Command Prompt         | `set OPENAI_API_KEY=sk-...`        | `setx OPENAI_API_KEY sk-...` (then open a new window)               |

2. **The private credentials file** in your configuration directory
   (`apex config path` shows it): `apex config set-key openai --env-file`.
   On Linux and macOS the file is created readable only by you. `apex setup` uses this
   file when no OS keyring is available.
3. **The OS keyring**, if the optional `keyring` package is installed (installer option
   `--with-keyring`): `apex config set-key openai`.

APEX CORE deliberately **ignores `.env` files in your working directory**: a repository
you clone must not be able to change where your API traffic goes.

## Using apex

| Command | What it does |
|---|---|
| `apex` | Open the chat in the current folder (runs setup the first time). Same as `apex chat`. |
| `apex setup` | Choose a provider, paste an API key, and pick a model. |
| `apex model` | Switch to another provider or model. |
| `apex init` | Set up from flags; without flags it is the same as `apex setup`. |
| `apex doctor [--online]` | Check Python, paths, config, API key, history, git, and terminal; `--online` also contacts the provider. |
| `apex config [show\|path\|get\|set\|unset\|set-key\|delete-key]` | View and change settings and stored keys. |
| `apex providers [list\|add\|remove\|use]` | Manage providers. |
| `apex models [-p PROVIDER]` | List models offered by a provider. |
| `apex ask "question"` | One question; the answer goes to stdout, status to stderr. |
| `apex task "description"` | Let the agent work on one task, approving each edit and command. |
| `apex tools` | List tools and whether each runs automatically or asks first. |
| `apex memory [show\|edit\|clear\|path]` | See, edit, or clear what the agent remembers. |
| `apex history [list\|show\|delete\|clear]` | View and delete local history. |

Chat options (after `apex` or `apex chat`): `-c/--continue`, `-r/--resume ID-OR-TITLE`,
`-p/--provider`, `-m/--model`, `-w/--workspace DIR` (default: the current directory),
`--read-only`, `--no-tools`, `--auto`, and `--no-history`. Run `apex <command> --help`
for the rest.

### Chat

| Key | What it does |
|---|---|
| Enter | Send. |
| Alt+Enter, Ctrl+J, or a line ending in `\` | New line. Pasted text keeps its lines. |
| `@path` | Attach a file from the workspace to your message (Tab completes the name). |
| Up / Down | Earlier messages. |
| `/` then Tab | Complete a slash command. |
| `!command` | Run a shell command yourself; the output is shown to you, not the model. |
| Ctrl+C | Stop the agent's current answer; at the prompt, clear the line; twice to exit. |
| Ctrl+D | Exit. |

Slash commands: `/help`, `/new` (also `/reset`, `/clear`), `/model [name|provider:name]`,
`/retry`, `/undo`, `/compress [focus]`, `/usage`, `/tools`, `/plan`, `/memory [clear]`,
`/personality [name|none]`, `/auto`, `/title NAME`, `/sessions`, `/resume ID-OR-TITLE`,
and `/exit` (also `/quit`). The [usage guide](docs/USAGE.md) explains each one.

The status bar under the prompt shows the model, the approximate context size (with a
fill bar if you set `context_window` for the provider), tokens and estimated cost, the
time of the last answer, the folder, and modes such as `AUTO` or `READ-ONLY`. Each answer
ends with a one-line summary of tools used, tokens, and time.

### Approvals

Before an edit you see the diff; before a command you see the exact program, arguments,
directory, and timeout:

```text
╭─ Run command? ───────────────────────────────────────────────────────────────╮
│ run python -m pytest -q                                                      │
│ program: /usr/bin/python                                                     │
│ directory: .                                                                 │
│ timeout: 120s                                                                │
╰──────────────────────────────────────────────────────────────────────────────╯
[o]nce | [s]ession | [a]lways | [d]eny  (Enter = deny, or type a reason)
Allow?
```

`o` allows it once. `s` allows it for the rest of this chat (all edits, or this exact
command). `a` always allows this exact command, saved to
`permissions.always_allowed_commands`. `d` or Enter denies it, and anything else you
type denies it and tells the agent why. Destructive or opaque commands (`rm`,
`git reset --hard`, `curl`, shells, `python -c`...) carry a warning and never offer
`s` or `a`.

`/auto` (or `--auto`) approves edits and ordinary commands for the rest of the chat;
flagged commands still ask, blocked ones are still refused, and without an interactive
terminal it has no effect.

### Ask

```bash
apex ask "Explain the difference between a process and a thread"
git diff | apex ask -                          # read the question from stdin
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

The same agent as the chat, for one request, with the same approval prompt. At the end
you get a table of every action and its result. Without an interactive terminal (in a
pipe or a CI job), anything that needs approval is refused, and those tools are not
offered to the model.

### Memory, project instructions, and persona

- **Memory.** The agent keeps two small notebooks, `MEMORY.md` (about your environment
  and projects) and `USER.md` (about you), and reads them at the start of each new
  conversation. You see a `💾` line in the feed whenever it saves something. Manage them
  with `/memory` or `apex memory`; turn them off with `memory.enabled = false`.
- **Project instructions.** An `APEX.md`, `AGENTS.md`, `CLAUDE.md`, or `.cursorrules`
  file at the workspace root is added to the agent's instructions (the first one found;
  `agent.context_files = false` turns this off). Files that look like prompt-injection
  attempts are not loaded, and the banner says so.
- **Persona.** `SOUL.md` in your configuration directory replaces the agent's default
  identity, and `/personality` (or `agent.personality`) changes its tone. Built in:
  concise, technical, teacher, creative, friendly, pirate. Add your own under
  `[agent.personalities]`.

### Exit codes

`0` success · `1` error (configuration, provider, network...) · `2` usage error ·
`130` cancelled with Ctrl+C.

## Configuration

Settings live in `config.toml`; history, memory, and input history in the data
directory:

| OS      | Configuration                                         | Data                                              |
|---------|-------------------------------------------------------|---------------------------------------------------|
| Linux   | `~/.config/apex-core/` (or `$XDG_CONFIG_HOME`)        | `~/.local/share/apex-core/` (or `$XDG_DATA_HOME`) |
| macOS   | `~/Library/Application Support/apex-core/`           | same                                              |
| Windows | `%LOCALAPPDATA%\apex-core\`                           | same                                              |

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
# context_window = 128000  # the model's context size, for the chat's context bar
# temperature = 0.2
# max_output_tokens = 4096

[providers.openai.pricing]   # optional: enables cost estimates
input_per_million = 0.0      # your price per 1M input tokens
output_per_million = 0.0     # your price per 1M output tokens
currency = "USD"

[agent]
max_steps = 12          # model calls per turn
personality = "concise" # optional; see /personality
context_files = true    # load AGENTS.md and friends from the workspace root

[agent.personalities]
reviewer = "Review like a strict senior engineer. Point out risks first."

[permissions]
write = "ask"           # ask | allow | deny
execute = "ask"         # ask | deny
memory = "allow"        # allow | ask | deny (the agent's own notes, never your files)
auto_approve_commands = [["git", "status"], ["python", "-m", "pytest"]]  # prefixes
always_allowed_commands = []   # exact commands you allowed with [a]lways
allow_elevated = false  # never run commands while apex runs as root/Administrator

[tools]
command_timeout = 120
max_output_chars = 20000
extra_secret_patterns = ["*.secret"]
extra_ignore_dirs = ["vendor"]

[history]
enabled = true
save_input = true       # remember typed messages for the up-arrow (redacted, private)

[memory]
enabled = true

[ui]
markdown = true         # render answers as Markdown in the chat
emoji = true            # emoji in the activity feed
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
for one shell session. Command-line options override everything. `APEX_SIMPLE_INPUT=1`
switches the chat to plain line input, for terminals where the rich input misbehaves.

## Security model

The short version (details and limits in [SECURITY.md](SECURITY.md)):

- **Workspace containment.** Tools only touch paths inside the workspace, after
  resolving `..` and symlinks. The `.git` directory and credentials files (`.env`,
  `*.pem`, `id_rsa`, `.npmrc`, ...) are blocked.
- **Approval.** Edits show a diff and commands show the exact argv before anything
  happens. Destructive or opaque commands (`rm`, `git reset --hard`, `bash -c`,
  `python -c`, ...) are flagged and are never approved automatically, not by
  `auto_approve_commands`, `[a]lways`, or `/auto`. Privilege escalation (`sudo`,
  `runas`...), disk formatting, and shutdown commands are always refused.
- **No shell.** Commands run from an argument list, with stdin closed, a timeout, an
  output cap, a working directory inside the workspace, and credential-like environment
  variables (`*_API_KEY`, `*TOKEN*`, `*SECRET*`...) removed. `!command` in the chat is
  the exception: it runs what *you* type in your shell, and refuses `sudo`.
- **`@file` attachments** follow the same rules as the read tool: inside the workspace
  only, never `.git` or credentials files, text only, redacted, and at most 100 KB.
- **Not as root.** If apex runs as root or an elevated Administrator, command execution
  is disabled.
- **Secrets.** Keys stay out of config files, logs, history, memory, and error messages;
  known token formats are redacted from tool output before it reaches the model.
- **Untrusted content.** File contents and command output are treated as data. Project
  instruction files, `SOUL.md`, and memory entries are scanned for prompt-injection
  patterns, but **prompt injection is not solved**: a malicious file can still persuade
  the model to *propose* harmful actions. Your approval is the safeguard, so read what
  you approve, and be careful with `/auto` in repositories you do not trust.

**This is not a sandbox.** An approved command runs with your full user permissions, and
running a project's tests runs that project's code. For untrusted repositories, use
`--read-only`, or run apex inside a container or VM.

## API costs

- Every model call is billed by your provider. Each tool step is another call that
  resends the conversation so far, so long tasks and long chats cost more.
  `agent.max_steps` (default 12) caps the calls per turn, and `/compress` shrinks a long
  chat.
- Token counts are shown after each chat answer, after `ask` and `task`, and by `/usage`,
  whenever the provider reports them.
- APEX CORE ships no price list, because prices change. Add `pricing` to a provider
  (see [Configuration](#configuration)) to see an estimate. Treat it as an estimate;
  your provider's billing page is authoritative.
- `apex models`, `apex doctor --online`, and the key check in `apex setup` make a
  model-listing request, which providers normally do not charge for.
- Ollama and LM Studio run on your machine and have no per-token cost.
- The automated tests never contact a paid API.

## Privacy

- No telemetry, analytics, update checks, or background processes.
- Conversations and task logs are stored only in your local SQLite file.
  `apex history delete <id>` and `apex history clear` remove them; deleted data is
  overwritten (`secure_delete`), and `clear` also compacts the file and deletes the
  typed-input history. Set `history.enabled = false` or pass `--no-history` to store
  nothing.
- Memory notes are plain files in the data directory (`apex memory path`). They are part
  of the instructions sent to your provider in every conversation.
- Typed chat input is kept for the up-arrow in a private file with known secrets masked;
  `history.save_input = false` turns it off.
- What leaves your machine: your messages, your memory notes and project instruction
  files, plus any file contents and command output the agent reads, are sent to the
  provider you configured. With a local provider (Ollama, LM Studio) nothing leaves
  your machine.
- The history file is not encrypted. On Linux and macOS it is readable only by you.

## Troubleshooting

| Problem | What to do |
|---|---|
| `apex: command not found` | Open a new terminal after installing. Check that `~/.local/bin` is on `PATH` (fish: `fish_add_path ~/.local/bin`). On Windows, check `%LOCALAPPDATA%\apex-core\bin`; with pipx, run `pipx ensurepath`. |
| pipx: `No module named 'filelock'` | The system pipx is broken: `sudo pacman -S python-filelock`, or use `scripts/install.sh`. |
| `No API key found` | Run `apex setup` and paste the key, or set the variable `apex doctor` names in the same shell you run apex from. On Windows, open a new window after `setx`. |
| `authentication failed (HTTP 401)` | The key is wrong or revoked. `apex doctor` shows where it was loaded from; `apex setup` replaces it. |
| `model not found` | `/model` in the chat, or `apex model`, lists what your key can use. |
| `rate limited` / `quota or credit exhausted` | apex retries rate limits automatically; quota errors mean checking the provider's billing settings. |
| `could not connect` | Check your network. For Ollama or LM Studio, make sure the server is running. Behind a proxy, set `HTTPS_PROXY`; for a custom CA, set `SSL_CERT_FILE`. |
| `request rejected` mentioning tools | The model does not support tool calling: use `--no-tools`, or `apex config set providers.<name>.supports_tools false`. |
| Garbled or duplicated streaming output | `apex config set providers.<name>.stream false`. |
| Arrow keys or the bottom bar misbehave | Set `APEX_SIMPLE_INPUT=1` to use plain line input. |
| Odd characters on Windows | Use Windows Terminal, or run `chcp 65001` in Command Prompt. `apex config set ui.emoji false` removes emoji. |
| Keyring prompts or hangs (headless Linux) | Set `APEX_NO_KEYRING=1` and use an environment variable or `--env-file`. |
| "command execution is disabled" | apex is running as root or an elevated Administrator. Run it as your normal user. |
| Edits or commands are always refused | You are not in an interactive terminal, `--read-only` is set, or `permissions.write/execute = "deny"`. |

## Platform status

CI runs on every push (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)):

| Platform | Python | How it is verified |
|---|---|---|
| Linux x86_64 (Ubuntu runner) | 3.11, 3.12, 3.13, 3.14 | Tests, lowest-dependency tests, package build, clean-install smoke test, and `install.sh` |
| Arch Linux (container) | 3.14 from pacman | Tests run as a normal user, and `install.sh` run from fish, including the PATH update for new fish sessions |
| macOS arm64 / Apple Silicon (`macos-latest`) | 3.11, 3.12, 3.13, 3.14 | Tests, package build, clean-install smoke test, and `install.sh` |
| macOS x86_64 / Intel (`macos-15-intel`) | 3.13 | Tests |
| Windows x86_64 (`windows-latest`) | 3.11, 3.12, 3.13, 3.14 | Tests, package build, clean-install smoke test, and `install.ps1` in Windows PowerShell 5.1 and PowerShell 7, as a file and through `Invoke-Expression` (the `irm ... \| iex` way) |
| CachyOS | (pacman's Python) | Not run in CI. CachyOS is based on Arch, which the Arch job covers. |

Last full verification, 2026-10-24, of version 0.2.0rc1 (commit `3bf2b80`): all 22 CI
jobs passed. Tests: 371 passed on Ubuntu 26.04 (Linux 7.0, glibc 2.43), Arch Linux
(Python 3.14.8, fish 4.9.3), macOS 26.6 (arm64), and macOS 15.7 (Intel); 375 passed on
Windows Server 2025 (build 26100, default cp1252 encoding). The skipped tests (7 on
Linux and macOS, 3 on Windows) apply only to the other operating systems. On every
platform this included a real Ctrl+C (Ctrl+Break on Windows) interrupting a hung
request, process-tree kills, symlink-escape checks, the git fsmonitor protection, and
the chat driven through prompt_toolkit with scripted keystrokes.

Results for the current commit are on the repository's
[Actions page](https://github.com/oliverhyar12-coder/Apex-Core/actions). Not covered by
CI: Windows on ARM, Linux on ARM, Windows 10 specifically (runners use Windows Server),
CachyOS itself, and interactive use in each terminal emulator. Provider adapters are
tested against mocked and local fake servers, not against the live services.

## Limitations

- Not yet published to PyPI; install from GitHub.
- Provider adapters are verified against mocked responses and a local fake server, not
  against live services. Real services can differ in details.
- No native Gemini or Ollama adapters (both are used through their OpenAI-compatible
  endpoints), no image or file attachments, and no extended "thinking" output.
- Tools: no file deletion or rename tool, no web access, no multi-file patch tool.
- Command execution is approval-gated but **not sandboxed** (see SECURITY.md).
- Long chats are compressed only when you run `/compress`; there is no automatic
  compression or per-task token budget yet. `agent.max_steps` is the main cost control.
- Memory is small by design (about 2,200 characters of notes and 1,375 about you), and
  past conversations are not searchable by the agent.
- Compared with Hermes Agent, there are no messaging gateways, skills, scheduled jobs,
  or subagents: APEX CORE is a terminal program only.
- The history database is not encrypted.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup on Linux, macOS, and Windows, plus
the test and lint commands. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) © 2026 Apex Team

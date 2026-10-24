# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0rc1] - not yet released

Second release candidate: a chat that works like a coding partner, modelled on Hermes
Agent, plus installers that do not need pipx. Not published to PyPI.

### Added

- A Hermes-style start screen: a blue block-letter APEX CORE title, a tiger logo, and a
  panel listing the tools, slash commands, and session; a welcome line and a tip. Plain
  text in terminals without colour or Unicode.
- A rule and `❯` prompt with example placeholders, and a status bar under it: model,
  context use (with a fill bar when `providers.<name>.context_window` is set), tokens,
  last answer time, folder, and `/ commands · @ files · ! shell`.
- `@path` attaches a workspace file to a message, with Tab completion; same rules as the
  read tool (workspace only, no `.git` or credentials files, text only, redacted).
- Markdown answers and the completion menu use the same blue theme.
- Running `apex` with no command opens the chat; the first run starts a setup wizard
  (choose a provider, paste the API key with hidden input, check it by listing models,
  pick a model). New `apex setup` and `apex model` commands.
- Chat input with prompt_toolkit: Enter sends; Alt+Enter, Ctrl+J, or a trailing `\`
  adds a line; Up/Down recall earlier messages (stored privately, with secrets masked);
  `/` completes commands with descriptions; a status bar shows the model, tokens, cost,
  context size, and mode. `APEX_SIMPLE_INPUT=1` falls back to plain input.
- Answers stream as rendered Markdown. A feed shows each tool as it runs, with an icon,
  timing, and the end of command output, and each turn ends with a one-line summary.
- Slash commands `/new`, `/model` (a picker, or `provider:name`), `/retry`, `/undo`,
  `/compress`, `/plan`, `/memory`, `/personality`, `/auto`, `/title`, `/sessions`, and
  `/resume`, and `!command` to run a shell command yourself.
- Approval scopes: allow once, for the session, or always (exact commands only, saved
  to the new `permissions.always_allowed_commands`), deny, or type feedback for the
  agent. Auto mode (`/auto`, `--auto`) approves edits and ordinary commands.
- `todo` tool: the agent keeps a visible plan for multi-step work.
- Long-term memory: the `memory` tool, two size-limited files (`MEMORY.md`, `USER.md`),
  `apex memory show|edit|clear|path`, and `memory.enabled` / `permissions.memory`.
- Project instructions from `APEX.md`, `AGENTS.md`, `CLAUDE.md`, or `.cursorrules` at
  the workspace root; a `SOUL.md` persona; built-in and custom personalities.
- Sessions: `apex -c/--continue`, `apex -r/--resume ID-OR-TITLE`, and titles.
- `scripts/install.sh` (Linux, macOS) and `scripts/install.ps1` (Windows): a private
  virtual environment, the `apex` command on PATH (bash, zsh, fish, Windows user PATH),
  update by re-running, and `--uninstall`.
- CI runs both installers on Linux, macOS, and Windows (PowerShell 5.1 and 7), and the
  tests plus the installer in an Arch Linux container with fish.
- [docs/USAGE.md](docs/USAGE.md), a guide to chatting and everyday use.
- Settings `agent.personality`, `agent.personalities`, `agent.context_files`,
  `history.save_input`, `ui.markdown`, and `ui.emoji`.

### Changed

- `apex` without a command opens the chat in an interactive terminal; elsewhere it
  still prints help. Chat options placed before another command
  (`apex --model x ask ...`) are a usage error that says where they go.
- Approval answers are `o` (once), `s` (session), `a` (always, saved), `d` (deny).
  **`a` used to mean "for this session"; it now saves the approval for later chats.**
  `y` still means once. Options that are not offered for an action ask again instead
  of being sent to the model as feedback.
- Ctrl+C on an empty chat prompt asks for a second press before leaving.
- `apex init` without flags runs the setup wizard in an interactive terminal.
- `apex history clear` also deletes the typed-input history.
- New dependency: `prompt_toolkit`.

### Security

- Project instruction files, `SOUL.md`, and memory entries are scanned for
  prompt-injection patterns (invisible characters, instruction overrides, hidden HTML
  comments, pipe-to-shell commands, credential exfiltration). Suspicious project files
  are not loaded; suspicious memory entries and secrets are refused.
- Auto mode never covers flagged commands, and without an interactive terminal it
  approves nothing.

## [0.1.0rc1] - not yet released

First release candidate of APEX CORE, the terminal AI agent. Not published to PyPI.

### Added

- `apex` command with `init`, `doctor`, `config`, `providers`, `models`, `chat`, `ask`,
  `task`, `tools`, and `history`.
- Provider interface with an OpenAI-compatible adapter (OpenAI, DeepSeek, Gemini via
  Google's compatibility endpoint, Mistral, OpenRouter, Groq, Ollama, LM Studio,
  custom servers) and a native Anthropic Messages API adapter. Both support streaming,
  tool calls, retries with backoff, and clear errors for authentication, rate limits,
  quotas, unknown models, and network failures.
- Configuration via Pydantic Settings: `config.toml` in the per-user configuration
  directory, `APEX_*` environment overrides, and validated `apex config set`.
- API keys from environment variables, a private credentials file, or the OS keyring
  (optional `keyring` extra). Never from `config.toml` or the working directory.
- Bounded agent loop (`agent.max_steps`) with tool-call validation, a permission
  policy, approval prompts with diffs, an action summary, and clean Ctrl+C
  cancellation.
- Tools: `list_files`, `read_file`, `search_files`, `git_status`, `git_diff` (read
  only); `edit_file` and `write_file` (approval required); `run_command` (approval
  required, argv only, no shell).
- Workspace containment (`..`, symlinks, `.git`, credentials files, Windows device
  names and alternate data streams), secret redaction, environment scrubbing, PATH
  hijack protection, process-tree termination, and refusal to execute commands as
  root/Administrator.
- Local SQLite history of chats, questions, and tasks, with `secure_delete`, plus
  `history list/show/delete/clear`.
- Token usage display and optional cost estimates from user-configured pricing.
- Test suite with mocked providers and a local fake server; GitHub Actions on Linux,
  macOS (arm64 and Intel), and Windows with Python 3.11 to 3.14, a lowest-dependency job,
  and a build-and-install smoke test on each OS.

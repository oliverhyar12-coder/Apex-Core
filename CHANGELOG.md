# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

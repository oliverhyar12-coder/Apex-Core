"""Numbered pickers for providers and models (used by setup and /model)."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from apexcore.providers.base import ModelInfo

# Model ids that are clearly not chat models (embeddings, speech, images...).
_NOT_CHAT = re.compile(
    r"(embed|tts|whisper|dall-?e|moderation|audio|realtime|transcribe|image|speech|"
    r"babbage|davinci-00|text-search|similarity|rerank|vision-preview|-search-)",
    re.IGNORECASE,
)
MAX_SHOWN = 40


def chat_models(models: Sequence[ModelInfo], preferred: str | None = None) -> list[ModelInfo]:
    """Filter a provider's model list down to likely chat models, preferred first."""
    chosen = [model for model in models if not _NOT_CHAT.search(model.id)] or list(models)
    chosen.sort(key=lambda model: (model.id != preferred, model.id))
    return chosen


def pick(
    console: Console,
    ask: Callable[[str], str],
    options: Sequence[tuple[str, str]],
    *,
    title: str,
    default: str | None = None,
    allow_custom: bool = True,
    custom_label: str = "type a name",
) -> str | None:
    """Show ``options`` (value, description) as a numbered list and return the
    chosen value, a typed custom value, or None if the user just pressed Enter
    with no default."""
    table = Table(title=title, title_justify="left", show_header=False, box=None, pad_edge=False)
    table.add_column(justify="right", style="cyan")
    table.add_column()
    table.add_column(style="dim")
    shown = list(options)[:MAX_SHOWN]
    for number, (value, description) in enumerate(shown, 1):
        marker = " (default)" if value == default else ""
        table.add_row(str(number), Text(value + marker), Text(description))
    console.print(table)
    if len(options) > MAX_SHOWN:
        console.print(Text(f"... and {len(options) - MAX_SHOWN} more", style="dim"))
    hint = "number"
    if allow_custom:
        hint += f" or {custom_label}"
    if default:
        hint += f", Enter for {default}"
    while True:
        answer = ask(f"Choose ({hint}): ").strip()
        if not answer:
            return default
        if answer.isdigit():
            index = int(answer) - 1
            if 0 <= index < len(shown):
                return shown[index][0]
            console.print(Text(f"Pick 1-{len(shown)}.", style="yellow"))
            continue
        if allow_custom:
            return answer
        matches = [value for value, _ in options if value == answer]
        if matches:
            return matches[0]
        console.print(Text("Not in the list.", style="yellow"))

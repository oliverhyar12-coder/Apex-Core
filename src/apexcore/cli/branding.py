"""APEX CORE's look: the block-letter title, the tiger logo, and the blue palette.

Both pictures need a Unicode terminal with colour; :func:`big_title` and
:func:`tiger_logo` return ``None`` when they would not display properly, and the
caller falls back to plain text.
"""

from __future__ import annotations

import random

from rich.console import Console
from rich.style import Style
from rich.text import Text
from rich.theme import Theme

# Blue theme.
SKY = "#7dd3fc"
BLUE = "#3b82f6"
DEEP = "#1e40af"
ACCENT = "#38bdf8"
MUTED = "#64748b"

# Markdown answers in the same colours (Rich's defaults are cyan-on-black code).
MARKDOWN_THEME = Theme(
    {
        "markdown.code": f"bold {SKY}",
        "markdown.h1": f"bold {ACCENT}",
        "markdown.h2": f"bold underline {ACCENT}",
        "markdown.h3": "bold #60a5fa",
        "markdown.h4": "bold #93c5fd",
        "markdown.item.bullet": f"bold {BLUE}",
        "markdown.item.number": f"bold {BLUE}",
        "markdown.link": "#60a5fa",
        "markdown.link_url": f"underline {BLUE}",
        "markdown.block_quote": "#93c5fd",
        "markdown.hr": DEEP,
    }
)

# "ANSI Shadow" letters: █ is the face, the box-drawing characters are the shadow.
_FONT: dict[str, tuple[str, ...]] = {
    "A": (" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"),
    "P": ("██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "),
    "E": ("███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"),
    "X": ("██╗  ██╗", "╚██╗██╔╝", " ╚███╔╝ ", " ██╔██╗ ", "██╔╝ ██╗", "╚═╝  ╚═╝"),
    "C": (" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"),
    "O": (" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "),
    "R": ("██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"),
    " ": ("  ",) * 6,
}
# Top-to-bottom gradient for the letter faces, and the shadow colour.
_TITLE_GRADIENT = ("#e0f2fe", "#bae6fd", "#7dd3fc", "#38bdf8", "#0ea5e9", "#0284c7")
_TITLE_SHADOW = "#1d4ed8"

# The tiger, as pixel art: one character per pixel, two pixels per text row.
# Each row is the left half; the right half is its mirror image.
_TIGER_COLORS = {
    "O": "#0b2a6f",  # outline and stripes
    "B": "#2f7df6",  # fur
    "W": "#eaf6ff",  # muzzle, brows, ears
    "E": "#22e3ff",  # eyes
    "K": "#020617",  # pupils
}
_TIGER = (
    "..OOOO.........",
    ".OBBBBO........",
    ".OBWWBBO..OOOOO",
    ".OBWWBBOOOBBBBB",
    "..OBBBBBBOBBOOO",
    "..OBBBBBOBBBBBB",
    "..OBOOBBBBBOOOO",
    ".OBBBBOBBBBBBBB",
    ".OBOBBBBWWWBBOO",
    "OBBBOBWWWWWWBBB",
    "OBOOBOOOOBBBBBB",
    "OBBBOEEEKOBBBBB",
    "OBOOBOEEKKOBBBB",
    "OBBBBBOOOOBBBWW",
    "OOOBBOBBBBBWWWW",
    "OBBOOBBBBWWWOOO",
    "OBOBBOBBWWWWOOO",
    "OOBOOBBWWWWWWOO",
    ".OBBBBWWWWOWWWO",
    "OBOOBWWWWWWOOOW",
    ".OBBBWWWWWWWWWW",
    "..OOBBWWWWWWWWW",
    "...OBBWWWWWWWWW",
    "....OOBBWWWWWWW",
    "......OOOBBBBBB",
    ".........OOOOOO",
)
TIGER_WIDTH = len(_TIGER[0]) * 2

TIPS = (
    "Alt+Enter (or Ctrl+J) starts a new line, and pasted text keeps its lines.",
    "apex -c continues your last chat in this folder.",
    "Type @ and a file name to attach that file to your message.",
    "!git status runs a command yourself; the agent does not see the output.",
    "/model switches models mid-chat, and /retry asks the new one again.",
    "Put your project's rules in AGENTS.md and the agent follows them here.",
    'Say "remember that ..." and the agent saves it to memory for next time.',
    "/compress summarizes a long chat so it costs fewer tokens.",
    "/auto lets edits and ordinary commands run without asking; risky ones still ask.",
    "At an approval prompt, type a reason instead of d and the agent reads it.",
    "/undo removes the last exchange; /new starts a fresh conversation.",
)

PLACEHOLDERS = (
    "Explain this project to me",
    "Find and fix the failing test",
    "Review my uncommitted changes",
    "Explain this error and how to fix it",
    "Add input validation to the signup form",
    "Write tests for @src/app.py",
)


def random_tip() -> str:
    return random.choice(TIPS)


def random_placeholder() -> str:
    return random.choice(PLACEHOLDERS)


def _fancy(console: Console) -> bool:
    encoding = (console.encoding or "").lower()
    return console.is_terminal and console.color_system is not None and "utf" in encoding


def title_lines(text: str = "APEX CORE") -> list[str]:
    letters = [_FONT[ch] for ch in text.upper()]
    return ["".join(letter[row] for letter in letters) for row in range(6)]


def big_title(console: Console, text: str = "APEX CORE") -> Text | None:
    """The block-letter title, or None if the terminal cannot show it."""
    lines = title_lines(text)
    if not _fancy(console) or console.width < max(map(len, lines)) + 2:
        return None
    title = Text(no_wrap=True, overflow="crop")
    for row, line in enumerate(lines):
        face = Style(color=_TITLE_GRADIENT[row], bold=True)
        shadow = Style(color=_TITLE_SHADOW)
        for ch in line:
            title.append(ch, face if ch == "█" else shadow)
        title.append("\n")
    title.rstrip()
    return title


def tiger_pixels() -> list[str]:
    return [row + row[::-1] for row in _TIGER]


def tiger_logo(console: Console) -> Text | None:
    """The tiger in half-block characters (two pixels per cell), or None."""
    if not _fancy(console):
        return None
    pixels = tiger_pixels()
    if len(pixels) % 2:
        pixels.append("." * len(pixels[0]))
    logo = Text(no_wrap=True, overflow="crop")
    for top_row, bottom_row in zip(pixels[::2], pixels[1::2], strict=True):
        for top_key, bottom_key in zip(top_row, bottom_row, strict=True):
            top = _TIGER_COLORS.get(top_key)
            bottom = _TIGER_COLORS.get(bottom_key)
            if top is None and bottom is None:
                logo.append(" ")
            elif bottom is None:
                logo.append("▀", Style(color=top))
            elif top is None:
                logo.append("▄", Style(color=bottom))
            elif top == bottom:
                logo.append("█", Style(color=top))
            else:
                logo.append("▀", Style(color=top, bgcolor=bottom))
        logo.append("\n")
    logo.rstrip()
    return logo

"""Command-line interface."""

from __future__ import annotations


def main() -> None:
    """Entry point for the ``apex`` console script."""
    from apexcore.cli.app import main as _main

    _main()


__all__ = ["main"]

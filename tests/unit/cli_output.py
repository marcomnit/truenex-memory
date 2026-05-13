"""Helpers for stable CLI assertions across local and GitHub Actions output."""

from __future__ import annotations

from click.utils import strip_ansi


def plain_cli_output(text: str) -> str:
    """Normalize Rich/Typer ANSI styling that GitHub Actions can force on."""
    return strip_ansi(text)

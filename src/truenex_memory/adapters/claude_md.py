"""CLAUDE.md adapter text generation."""

from __future__ import annotations


def generate_claude_md() -> str:
    """Return concise Claude Code instructions for Truenex Memory."""

    return "\n".join(
        [
            "# Truenex Memory",
            "",
            "Before coding, search local memory for project decisions and constraints.",
            "Prefer active memory results and treat unverified results as tentative.",
            "Do not use obsolete or superseded memory unless explicitly asked.",
        ]
    )

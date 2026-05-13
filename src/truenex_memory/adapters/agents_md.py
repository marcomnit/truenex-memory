"""AGENTS.md adapter text generation."""

from __future__ import annotations


def generate_agents_md() -> str:
    """Return concise instructions for agents using Truenex Memory."""

    return "\n".join(
        [
            "# Agent Memory",
            "",
            "Before making project claims, query Truenex Memory for relevant constraints.",
            "Use `memory_search` for decisions, architecture notes, and project conventions.",
            "Cite local source paths returned by memory results when they affect the answer.",
        ]
    )

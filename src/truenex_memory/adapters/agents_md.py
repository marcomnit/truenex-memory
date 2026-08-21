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
            (
                "Pass the project you are working in as `scope` (a path fragment, "
                "e.g. the repository folder name). The store holds every project "
                "at once, so an unscoped search competes against roughly eighty "
                "times more candidates and usually answers with another "
                "project's documents. Omit `scope` only for deliberately "
                "cross-project questions such as \"where did I solve this "
                "before?\" — and note that a WRONG scope returns that other "
                "project's documents rather than nothing, so pass the folder you "
                "are actually in."
            ),
            "Cite local source paths returned by memory results when they affect the answer.",
        ]
    )

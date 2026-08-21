"""CLAUDE.md adapter text generation."""

from __future__ import annotations


def generate_claude_md() -> str:
    """Return concise Claude Code instructions for Truenex Memory."""

    return "\n".join(
        [
            "# Truenex Memory",
            "",
            "Before coding, search local memory for project decisions and constraints.",
            (
                "Pass the project you are working in as `scope` (a path "
                "fragment, e.g. the repository folder name). The store holds "
                "every project at once, so an unscoped search competes against "
                "roughly eighty times more candidates and usually answers with "
                "another project's documents: measured on questions phrased "
                "without the target document's own words, scoping tripled the "
                "answers found. Omit `scope` only for deliberately "
                "cross-project questions."
            ),
            (
                "A WRONG scope returns that other project's documents rather "
                "than nothing, so pass the folder you are actually in."
            ),
            "Prefer active memory results and treat unverified results as tentative.",
            "Do not use obsolete or superseded memory unless explicitly asked.",
        ]
    )

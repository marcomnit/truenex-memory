"""CLAUDE.md adapter text generation.

Delegates to `profile`: this module used to carry its own hand-written copy of
the instructions, and so did `agents_md`, and the two had already drifted —
one said "prefer active results, do not use obsolete memory", the other said
"cite local source paths", and nobody decided either. Two copies of a rule are
two rules.
"""

from __future__ import annotations

from truenex_memory.adapters.profile import render_block


def generate_claude_md() -> str:
    """Return the memory profile block, for Claude Code's user-level file."""

    return render_block()

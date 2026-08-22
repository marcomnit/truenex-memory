"""AGENTS.md adapter text generation.

Delegates to `profile` for the same reason `claude_md` does: one source, or
the copies drift. See that module's docstring.
"""

from __future__ import annotations

from truenex_memory.adapters.profile import render_block


def generate_agents_md() -> str:
    """Return the memory profile block, for an AGENTS.md-reading client."""

    return render_block()

"""Git Bridge — error types and helpers for git-based memory sync."""

from __future__ import annotations


class GitBridgeError(Exception):
    """Raised when a git bridge operation fails."""

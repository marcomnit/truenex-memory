"""Unified source exclusion logic with .gitignore support."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

DEFAULT_INDEX_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".py", ".toml", ".yaml", ".yml", ".json",
    ".rst", ".cfg", ".ini",
}

DEFAULT_EXCLUDED_DIRS = {
    ".agent", ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".truenex-memory", "node_modules", ".mypy_cache", ".tox",
    ".pytest-tmp", "pytest_tmp", ".task_work", ".task3_work",
    "site-packages", "dist-info", ".conda", "conda-meta",
    "dist", "build", ".eggs", ".ruff_cache", ".coverage",
    ".idea", ".vscode", ".history", ".DS_Store",
    # Agent worktrees (e.g. `.claude/worktrees/agent-*`) are ephemeral
    # copies of the repository: indexing them makes search return outdated
    # duplicates of files that changed in the real working tree.
    # The exclusion matches ANY directory named `worktrees` at any depth,
    # not only `.claude/worktrees`.
    "worktrees",
}

DEFAULT_EXCLUDED_DIR_PREFIXES = (
    "task_work_", "pytest-task", "pytest-cache-files-", "venv",
)

DEFAULT_EXCLUDED_FILENAMES = frozenset({
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "special_tokens_map.json",
    "generation_config.json",
    "merges.txt",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "composer.lock",
    "Pipfile.lock",
    "poetry.lock",
    "Gemfile.lock",
    "package.json",
    "uv.lock",
    "Cargo.lock",
    "go.sum",
})


def _gitignore_pattern_to_regex(pattern: str) -> str | None:
    """Convert a single gitignore-style pattern to an fnmatch pattern."""
    pattern = pattern.rstrip("\n\r")
    if not pattern or pattern.startswith("#"):
        return None
    # Negation not supported in this minimal parser
    if pattern.startswith("!"):
        return None
    # Handle directory-only patterns
    is_dir = pattern.endswith("/")
    if is_dir:
        pattern = pattern[:-1]
    # If pattern contains a slash (and is not just leading), it is anchored
    has_slash = "/" in pattern.lstrip("/")
    # Normalize leading slash
    pattern = pattern.lstrip("/")
    return pattern, is_dir, has_slash


def load_gitignore_patterns(root: Path) -> list[tuple[str, bool, bool]]:
    """Parse .gitignore in *root* and return list of (fnmatch_pattern, is_dir, anchored)."""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []
    try:
        lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    patterns: list[tuple[str, bool, bool]] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            continue
        parsed = _gitignore_pattern_to_regex(line)
        if parsed is not None:
            patterns.append(parsed)
    return patterns


def _match_gitignore(rel_path: str, name: str, is_dir: bool, patterns: list[tuple[str, bool, bool]]) -> bool:
    for pat, pat_dir, anchored in patterns:
        if pat_dir and not is_dir:
            continue
        if anchored:
            # Match relative path from root
            if fnmatch.fnmatch(rel_path, pat):
                return True
            # Also try with /** suffix for directories
            if is_dir and fnmatch.fnmatch(rel_path + "/", pat):
                return True
        else:
            # Match any path component
            if fnmatch.fnmatch(name, pat):
                return True
            # Also match against the full relative path for unanchored patterns with /
            if "/" in pat and fnmatch.fnmatch(rel_path, pat):
                return True
    return False


def should_exclude(
    path: Path,
    *,
    root: Path,
    extra_dirs: set[str] | None = None,
    extra_filenames: set[str] | None = None,
    gitignore_patterns: list[tuple[str, bool, bool]] | None = None,
) -> bool:
    """Return True if *path* should be excluded from indexing/ingestion."""
    try:
        rel = path.relative_to(root)
        rel_path = rel.as_posix()
        parts = rel.parts
    except ValueError:
        rel_path = path.as_posix()
        parts = path.parts

    name = path.name
    is_dir = path.is_dir()

    excluded_dirs = DEFAULT_EXCLUDED_DIRS | (extra_dirs or set())
    excluded_filenames = DEFAULT_EXCLUDED_FILENAMES | (extra_filenames or set())

    # Check directory parts (relative to root)
    for part in parts:
        if part in excluded_dirs:
            return True
        if any(part.startswith(prefix) for prefix in DEFAULT_EXCLUDED_DIR_PREFIXES):
            return True

    # Check filename
    if not is_dir and name in excluded_filenames:
        return True

    # Check .gitignore patterns
    if gitignore_patterns:
        if _match_gitignore(rel_path, name, is_dir, gitignore_patterns):
            return True

    return False

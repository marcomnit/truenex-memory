"""Parser for text-based project documentation sources.

Handles source_type=project_docs. Walks a directory tree, filters to
supported text extensions, and produces one IngestionRecord per file.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from truenex_memory.ingestion.manifest import IngestionRecord
from truenex_memory.ingestion.parsers import register

INDEX_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".py", ".toml", ".yaml", ".yml", ".json",
    ".rst", ".cfg", ".ini",
}
EXCLUDED_DIRS = {
    ".agent", ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".truenex-memory", "node_modules", ".mypy_cache", ".tox",
    ".pytest-tmp", "pytest_tmp", ".task_work", ".task3_work",
    "site-packages", "dist-info", ".conda", "conda-meta",
    "dist", "build", ".eggs", ".ruff_cache", ".coverage",
}
EXCLUDED_DIR_PREFIXES = ("task_work_", "pytest-task", "pytest-cache-files-", "venv")

EXCLUDED_FILENAMES: frozenset[str] = frozenset({
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
})

MIN_ALPHA_RATIO = 0.35


@register("project_docs")
def parse_project_docs(
    source_dir: Path,
    project: str,
    source_tool: str,
    privacy_scope: str,
) -> list[IngestionRecord]:
    """Walk a directory and create records for supported text files."""
    records: list[IngestionRecord] = []
    resolved = source_dir.resolve()
    if not resolved.exists():
        return records
    candidates = _iter_candidate_files(resolved)

    for file_path in candidates:
        suffix = file_path.suffix.lower()
        if suffix not in INDEX_EXTENSIONS:
            continue
        if file_path.name in EXCLUDED_FILENAMES:
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        alpha_count = sum(1 for c in text if c.isalpha())
        if len(text) > 0 and alpha_count / len(text) < MIN_ALPHA_RATIO:
            continue
        mtime = _file_mtime_iso(file_path)
        records.append(
            IngestionRecord(
                project=project,
                source_type="project_docs",
                source_path=str(file_path.resolve()),
                source_tool=source_tool,
                text=text,
                created_at=mtime,
                last_modified=mtime,
                privacy_scope=privacy_scope,
            )
        )
    return records


def _iter_candidate_files(resolved: Path) -> list[Path]:
    """Yield files while pruning excluded directories before descent."""
    if resolved.is_file():
        return [resolved]

    candidates: list[Path] = []
    for root, dirnames, filenames in os.walk(resolved):
        dirnames[:] = [
            name for name in dirnames
            if not _is_excluded_dir_name(name)
        ]
        root_path = Path(root)
        for filename in filenames:
            candidates.append(root_path / filename)
    return sorted(candidates)


def _is_excluded_path(path: Path, *, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    for part in parts:
        if _is_excluded_dir_name(part):
            return True
    return False


def _is_excluded_dir_name(part: str) -> bool:
    if part in EXCLUDED_DIRS:
        return True
    return any(part.startswith(prefix) for prefix in EXCLUDED_DIR_PREFIXES)


def _file_mtime_iso(path: Path) -> str:
    try:
        stat = path.stat()
        return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return datetime.now(timezone.utc).isoformat()

"""Parser for text-based project documentation sources.

Handles source_type=project_docs. Walks a directory tree, filters to
supported text extensions, and produces one IngestionRecord per file.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from truenex_memory.core.exclusions import (
    DEFAULT_INDEX_EXTENSIONS,
    DEFAULT_EXCLUDED_FILENAMES,
    load_gitignore_patterns,
    should_exclude,
)
from truenex_memory.ingestion.manifest import IngestionRecord
from truenex_memory.ingestion.parsers import register

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
    gitignore = load_gitignore_patterns(resolved)
    candidates = _iter_candidate_files(resolved, gitignore=gitignore)

    for file_path in candidates:
        suffix = file_path.suffix.lower()
        if suffix not in DEFAULT_INDEX_EXTENSIONS:
            continue
        if file_path.name in DEFAULT_EXCLUDED_FILENAMES:
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


def _iter_candidate_files(resolved: Path, gitignore: list | None = None) -> list[Path]:
    """Yield files while pruning excluded directories before descent."""
    if resolved.is_file():
        return [resolved]

    candidates: list[Path] = []
    for root, dirnames, filenames in os.walk(resolved):
        root_path = Path(root)
        # Prune excluded directories
        dirnames[:] = [
            name for name in dirnames
            if not should_exclude(root_path / name, root=resolved, gitignore_patterns=gitignore)
        ]
        for filename in filenames:
            file_path = root_path / filename
            if should_exclude(file_path, root=resolved, gitignore_patterns=gitignore):
                continue
            candidates.append(file_path)
    return sorted(candidates)


def _file_mtime_iso(path: Path) -> str:
    try:
        stat = path.stat()
        return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return datetime.now(timezone.utc).isoformat()

"""Local file indexing."""

from __future__ import annotations

import os
from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.core.exclusions import (
    DEFAULT_INDEX_EXTENSIONS,
    load_gitignore_patterns,
    should_exclude,
)
from truenex_memory.store.repository import MemoryRepository


def index_path(
    path: Path,
    *,
    project_root: Path,
    repository: MemoryRepository,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    extra_dirs: set[str] | None = None,
    extra_filenames: set[str] | None = None,
) -> int:
    """Index supported files under a path into the local SQLite store."""

    target = path.resolve()
    gitignore = load_gitignore_patterns(project_root)
    files = [target] if target.is_file() else list(_iter_indexable_files(target, root_dir=project_root, extra_dirs=extra_dirs, extra_filenames=extra_filenames, gitignore=gitignore))
    indexed = 0
    _chunk_size = chunk_size if chunk_size is not None else 1200
    _chunk_overlap = chunk_overlap if chunk_overlap is not None else 0
    for file_path in files:
        if file_path.suffix.lower() not in DEFAULT_INDEX_EXTENSIONS:
            continue
        text = file_path.read_text(encoding="utf-8", errors="replace")
        chunks = chunk_text(text, max_chars=_chunk_size, overlap=_chunk_overlap)
        if not chunks:
            continue
        try:
            relative_path = str(file_path.resolve().relative_to(project_root.resolve()))
        except ValueError:
            relative_path = str(file_path.resolve())
        repository.upsert_document(file_path, relative_path, chunks)
        indexed += 1
    return indexed


def _iter_indexable_files(
    root: Path,
    *,
    root_dir: Path | None = None,
    extra_dirs: set[str] | None = None,
    extra_filenames: set[str] | None = None,
    gitignore: list | None = None,
):
    if root_dir is None:
        root_dir = root
    for dirpath, dirnames, filenames in os.walk(root):
        dir_path = Path(dirpath)
        # Prune excluded directories
        dirnames[:] = [
            name for name in dirnames
            if not should_exclude(dir_path / name, root=root_dir, extra_dirs=extra_dirs, extra_filenames=extra_filenames, gitignore_patterns=gitignore)
        ]
        for filename in filenames:
            file_path = dir_path / filename
            if should_exclude(file_path, root=root_dir, extra_dirs=extra_dirs, extra_filenames=extra_filenames, gitignore_patterns=gitignore):
                continue
            yield file_path

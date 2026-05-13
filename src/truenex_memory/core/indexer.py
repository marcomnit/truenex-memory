"""Local file indexing."""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.store.repository import MemoryRepository


INDEX_EXTENSIONS = {".md", ".markdown", ".txt", ".py", ".toml", ".yaml", ".yml", ".json"}
EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".truenex-memory"}


def index_path(path: Path, *, project_root: Path, repository: MemoryRepository) -> int:
    """Index supported files under a path into the local SQLite store."""

    target = path.resolve()
    files = [target] if target.is_file() else list(_iter_indexable_files(target))
    indexed = 0
    for file_path in files:
        if file_path.suffix.lower() not in INDEX_EXTENSIONS:
            continue
        text = file_path.read_text(encoding="utf-8", errors="replace")
        chunks = chunk_text(text)
        if not chunks:
            continue
        try:
            relative_path = str(file_path.resolve().relative_to(project_root.resolve()))
        except ValueError:
            relative_path = str(file_path.resolve())
        repository.upsert_document(file_path, relative_path, chunks)
        indexed += 1
    return indexed


def _iter_indexable_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        yield path

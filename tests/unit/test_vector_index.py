"""Tests for the in-process numpy vector index."""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.core.chunker import chunk_text
from truenex_memory.retrieval import vector_index
from truenex_memory.retrieval.vector_index import (
    clear_cache,
    get_index,
    search_index,
)
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.sqlite import connect


class _StubEmbedder:
    """Deterministic semantic embedder stub (8d, content-hash direction)."""

    def __init__(self, dimensions: int = 8) -> None:
        self._dimensions = dimensions

    @property
    def model_name(self) -> str:
        return "stub-semantic:vector-index-test"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _vector(self, seed: int) -> list[float]:
        vector = [0.0] * self._dimensions
        vector[seed % self._dimensions] = 1.0
        return vector

    def embed(self, text: str) -> list[float]:
        return self._vector(len(text))

    def embed_query(self, text: str) -> list[float]:
        return self.embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


@pytest.fixture()
def indexed_db(tmp_path: Path) -> Path:
    clear_cache()
    repository = MemoryRepository(tmp_path / "memory.db", embedder=_StubEmbedder())
    for index in range(3):
        doc = tmp_path / f"doc_{index}.md"
        # Distinct lengths -> distinct one-hot directions.
        doc.write_text("contenuto " + "x" * (index + 1), encoding="utf-8")
        repository.upsert_document(doc, f"docs/doc_{index}.md", chunk_text(doc.read_text()))
    yield tmp_path / "memory.db"
    clear_cache()


def test_index_builds_and_searches(indexed_db: Path) -> None:
    embedder = _StubEmbedder()
    conn = connect(indexed_db)
    try:
        entry = get_index(indexed_db, conn, embedder.model_name)
        assert entry is not None
        assert entry.matrix.shape[0] == entry.vector_count
        assert entry.matrix.shape[1] == embedder.dimensions

        query = embedder.embed_query("contenuto xx")  # same direction as doc_1
        matches = search_index(entry, query, top_k=2)
        assert matches
        assert matches[0].score > 0.9, "one-hot query must match its own direction"
        # point ids resolve to real chunks
        assert all(match.point_id in set(entry.point_ids) for match in matches)
    finally:
        conn.close()


def test_index_cache_hit_and_invalidation(indexed_db: Path) -> None:
    embedder = _StubEmbedder()
    conn = connect(indexed_db)
    try:
        first = get_index(indexed_db, conn, embedder.model_name)
        second = get_index(indexed_db, conn, embedder.model_name)
        assert first is second, "same mtime+count must reuse the cached entry"

        # A write that changes the model's chunk set (count and/or
        # max(updated_at)) triggers a rebuild. Note: DB mtime is NOT the
        # criterion — search() itself writes retrieval_logs, which would
        # invalidate after every query.
        repository = MemoryRepository(indexed_db, embedder=embedder)
        doc = indexed_db.parent / "extra.md"
        doc.write_text("contenuto extra yyyy", encoding="utf-8")
        repository.upsert_document(doc, "docs/extra.md", chunk_text(doc.read_text()))

        third = get_index(indexed_db, conn, embedder.model_name)
        assert third is not first
        assert third.vector_count > first.vector_count
    finally:
        conn.close()


def test_index_returns_none_for_unknown_model(indexed_db: Path) -> None:
    conn = connect(indexed_db)
    try:
        assert get_index(indexed_db, conn, "sentence-transformers:no-such-model") is None
    finally:
        conn.close()


def test_search_rejects_dimension_mismatch(indexed_db: Path) -> None:
    conn = connect(indexed_db)
    try:
        entry = get_index(indexed_db, conn, _StubEmbedder().model_name)
        assert entry is not None
        assert search_index(entry, [1.0] * 768, 5) == [], (
            "cross-dimension cosine must never happen"
        )
    finally:
        conn.close()


def test_numpy_missing_falls_back(indexed_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vector_index, "NUMPY_AVAILABLE", False)
    conn = connect(indexed_db)
    try:
        assert get_index(indexed_db, conn, _StubEmbedder().model_name) is None, (
            "without numpy the caller must fall back to the legacy Python scan"
        )
    finally:
        conn.close()

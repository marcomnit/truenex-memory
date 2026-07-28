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


def test_persistent_cache_roundtrip_uses_memmap(indexed_db: Path) -> None:
    """First build writes the npy+sidecar; after clear_cache() the next
    get_index must reopen the memmap (from_memmap) with identical content."""
    import numpy as np

    embedder = _StubEmbedder()
    conn = connect(indexed_db)
    try:
        built = get_index(indexed_db, conn, embedder.model_name)
        assert built is not None and not built.from_memmap
        matrix_path, sidecar_path = vector_index._persistent_cache_paths(
            indexed_db, embedder.model_name
        )
        assert matrix_path.exists() and sidecar_path.exists()

        clear_cache()
        loaded = get_index(indexed_db, conn, embedder.model_name)
        assert loaded is not None
        assert loaded.from_memmap, "second process must use the persistent memmap"
        assert isinstance(loaded.matrix, np.memmap)
        assert loaded.point_ids == built.point_ids
        assert loaded.vector_count == built.vector_count

        query = embedder.embed_query("contenuto xx")
        assert search_index(loaded, query, top_k=2) == search_index(built, query, top_k=2)
    finally:
        conn.close()


def test_persistent_cache_invalidated_on_chunk_update(indexed_db: Path) -> None:
    """A chunk-set mutation bumps MAX(updated_at) -> stale sidecar -> rebuild
    with a rewritten cache."""
    embedder = _StubEmbedder()
    conn = connect(indexed_db)
    try:
        first = get_index(indexed_db, conn, embedder.model_name)
        assert first is not None
        clear_cache()

        repository = MemoryRepository(indexed_db, embedder=embedder)
        doc = indexed_db.parent / "extra.md"
        doc.write_text("contenuto extra yyyy", encoding="utf-8")
        repository.upsert_document(doc, "docs/extra.md", chunk_text(doc.read_text()))

        rebuilt = get_index(indexed_db, conn, embedder.model_name)
        assert rebuilt is not None
        assert not rebuilt.from_memmap, "stale sidecar must trigger a slow rebuild"
        assert rebuilt.vector_count > first.vector_count

        # The cache was rewritten: the next cold load is a memmap again.
        clear_cache()
        reloaded = get_index(indexed_db, conn, embedder.model_name)
        assert reloaded is not None and reloaded.from_memmap
        assert reloaded.vector_count == rebuilt.vector_count
    finally:
        conn.close()


def test_persistent_cache_corrupt_sidecar_falls_back(
    indexed_db: Path,
) -> None:
    """A corrupt sidecar must never crash: slow build + cache rewrite."""
    embedder = _StubEmbedder()
    conn = connect(indexed_db)
    try:
        assert get_index(indexed_db, conn, embedder.model_name) is not None
        clear_cache()
        _, sidecar_path = vector_index._persistent_cache_paths(
            indexed_db, embedder.model_name
        )
        sidecar_path.write_text("{not json", encoding="utf-8")

        entry = get_index(indexed_db, conn, embedder.model_name)
        assert entry is not None and not entry.from_memmap
        # Sidecar rewritten by the fallback build.
        clear_cache()
        assert get_index(indexed_db, conn, embedder.model_name).from_memmap
    finally:
        conn.close()


def test_persistent_cache_write_failure_is_graceful(
    indexed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.replace failing (e.g. another process memmapping the file on
    Windows) must not fail the search: the slow-build entry is returned."""
    import truenex_memory.retrieval.vector_index as vi

    monkeypatch.setattr(vi.os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    conn = connect(indexed_db)
    try:
        entry = get_index(indexed_db, conn, _StubEmbedder().model_name)
        assert entry is not None and not entry.from_memmap
    finally:
        conn.close()


def test_persistent_cache_dropped_when_model_has_no_vectors(indexed_db: Path) -> None:
    """When the model's chunks disappear, stale cache files are removed so a
    later re-embed cannot resurrect orphaned data."""
    embedder = _StubEmbedder()
    conn = connect(indexed_db)
    try:
        assert get_index(indexed_db, conn, embedder.model_name) is not None
        matrix_path, sidecar_path = vector_index._persistent_cache_paths(
            indexed_db, embedder.model_name
        )
        assert matrix_path.exists()
        clear_cache()
        conn.execute("DELETE FROM chunks WHERE embedding_model = ?", (embedder.model_name,))
        conn.commit()
        assert get_index(indexed_db, conn, embedder.model_name) is None
        assert not matrix_path.exists() and not sidecar_path.exists()
    finally:
        conn.close()


def test_tmp_sweep_removes_only_old_orphans(tmp_path: Path) -> None:
    """GC: tmp files older than the age guard are removed (crashed writer),
    recent ones are kept (concurrent active writer must never lose its tmp)."""
    import os
    import time

    cache_dir = tmp_path / "vector_cache"
    cache_dir.mkdir()
    old_tmp = cache_dir / "model.npy.11111.tmp"
    fresh_tmp = cache_dir / "model.npy.22222.tmp"
    keep_file = cache_dir / "model.npy"
    for path in (old_tmp, fresh_tmp, keep_file):
        path.write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(old_tmp, (two_hours_ago, two_hours_ago))

    removed = vector_index._sweep_stale_tmp_files(cache_dir)

    assert removed == 1
    assert not old_tmp.exists(), "orphan tmp older than 1h must be collected"
    assert fresh_tmp.exists(), "a recent tmp (active writer) must be preserved"
    assert keep_file.exists()
    # Idempotent and crash-proof on a missing directory.
    assert vector_index._sweep_stale_tmp_files(tmp_path / "no-such-dir") == 0

"""Tests for the resumable reindex-embeddings batch job."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.ingestion.reindex_embeddings import (
    count_reindex_targets,
    reindex_embeddings,
)
from truenex_memory.store.repository import MemoryRepository


class _StubReindexEmbedder:
    """Deterministic document embedder stub (no downloads).

    model_name does NOT start with "hashing-fallback:", so the dense RRF
    ranker is active when this stub backs a MemoryRepository."""

    def __init__(self, dimensions: int = 8) -> None:
        self._dimensions = dimensions
        self.embed_documents_calls: list[list[str]] = []
        self.embed_calls: list[str] = []

    @property
    def model_name(self) -> str:
        return f"stub-semantic:reindex-test-{self._dimensions}d"

    def embed(self, text: str) -> list[float]:
        self.embed_calls.append(text)
        return [1.0] + [0.0] * (self._dimensions - 1)

    def embed_query(self, text: str) -> list[float]:
        return self.embed(f"query: {text}")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embed_documents_calls.append(list(texts))
        return [[1.0] + [0.0] * (self._dimensions - 1) for _ in texts]


def _repository_with_chunks(tmp_path: Path, doc_count: int) -> MemoryRepository:
    repository = MemoryRepository(tmp_path / "memory.db")
    for index in range(doc_count):
        doc = tmp_path / f"doc_{index}.md"
        doc.write_text(
            f"# Documento {index}\n\nContenuto alpha beta gamma numero {index} "
            "con abbastanza testo da generare chunk multipli. " * 20,
            encoding="utf-8",
        )
        repository.upsert_document(doc, f"docs/doc_{index}.md", chunk_text(doc.read_text()))
    return repository


def test_reindex_dry_run_only_counts(tmp_path: Path) -> None:
    _repository_with_chunks(tmp_path, doc_count=2)
    embedder = _StubReindexEmbedder()

    report = reindex_embeddings(tmp_path / "memory.db", embedder=embedder, dry_run=True)

    assert report.dry_run
    assert report.total_chunks > 0
    assert report.already_current == 0
    assert report.to_reindex == report.total_chunks
    assert report.processed == 0


def test_reindex_is_resumable_across_runs(tmp_path: Path) -> None:
    """Critical requirement: a partial run (limit) commits its batches, and
    a second run completes the rest — already-migrated chunks are skipped."""
    _repository_with_chunks(tmp_path, doc_count=2)
    db_path = tmp_path / "memory.db"
    embedder = _StubReindexEmbedder()
    total = count_reindex_targets(sqlite3.connect(str(db_path)), embedder.model_name)[0]
    assert total >= 2, "test premise: need at least 2 chunks"

    first = reindex_embeddings(
        db_path, embedder=embedder, batch_size=1, limit=2, dry_run=False
    )
    assert first.processed == 2
    conn = sqlite3.connect(str(db_path))
    try:
        _, current, remaining = count_reindex_targets(conn, embedder.model_name)
    finally:
        conn.close()
    assert current == 2
    assert remaining == total - 2

    second = reindex_embeddings(db_path, embedder=embedder, batch_size=3, dry_run=False)
    assert second.processed == total - 2

    final = reindex_embeddings(db_path, embedder=embedder, dry_run=True)
    assert final.already_current == total
    assert final.to_reindex == 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT embedding_model, embedding_vector_json FROM chunks"
        ).fetchall()
        assert all(row["embedding_model"] == embedder.model_name for row in rows)
        import json

        dims = {len(json.loads(row["embedding_vector_json"])) for row in rows}
        assert dims == {embedder._dimensions}
    finally:
        conn.close()


def test_reindex_validates_parameters(tmp_path: Path) -> None:
    import pytest

    _repository_with_chunks(tmp_path, doc_count=1)
    embedder = _StubReindexEmbedder()
    with pytest.raises(ValueError, match="batch_size"):
        reindex_embeddings(tmp_path / "memory.db", embedder=embedder, batch_size=0)
    with pytest.raises(ValueError, match="limit"):
        reindex_embeddings(tmp_path / "memory.db", embedder=embedder, limit=0)


def test_reindex_backfills_point_id_and_chunks_become_dense_searchable(tmp_path: Path) -> None:
    """Blocker-1 e2e: chunks indexed WITHOUT an embedder (qdrant_point_id
    NULL, no vector) after reindex carry the derived point id (same
    chunk_point_id derivation as upsert_document) AND surface through the
    dense RRF ranker in search()."""
    from truenex_memory.retrieval.semantic import chunk_point_id
    from truenex_memory.retrieval.vector_index import clear_cache

    # Index WITHOUT any embedder: no vectors, NULL point ids.
    repository = MemoryRepository(tmp_path / "memory.db")
    doc = tmp_path / "solo.md"
    doc.write_text("zzz yyy xxx www vvv contenuto solo denso.", encoding="utf-8")
    repository.upsert_document(doc, "docs/solo.md", chunk_text(doc.read_text()))

    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    try:
        null_points = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE qdrant_point_id IS NULL"
        ).fetchone()[0]
        assert null_points > 0, "test premise: chunks must start with NULL point ids"
    finally:
        conn.close()

    embedder = _StubReindexEmbedder()
    report = reindex_embeddings(tmp_path / "memory.db", embedder=embedder, dry_run=False)
    assert report.processed == null_points
    assert report.point_ids_backfilled == null_points

    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id, qdrant_point_id FROM chunks").fetchall()
        assert all(row["qdrant_point_id"] == chunk_point_id(row["id"]) for row in rows), (
            "backfilled point ids must match the upsert_document derivation"
        )
    finally:
        conn.close()

    # search() with the stub semantic embedder: the query shares NO token
    # with the chunk, so only the dense ranker can surface it.
    clear_cache()
    semantic_repo = MemoryRepository(tmp_path / "memory.db", embedder=embedder)
    hits = semantic_repo.search("alpha beta gamma delta", top_k=5)
    assert hits, "dense ranker must return the re-embedded chunks"
    assert hits[0].source_path.endswith("solo.md")
    assert hits[0].memory_type == "document_chunk"


def test_upsert_document_uses_embed_documents_prefix(tmp_path: Path) -> None:
    """Warning-4: upsert_document must embed chunks via embed_documents
    (e5 'passage: ' prefix), not via the unprefixed generic embed()."""
    embedder = _StubReindexEmbedder()
    repository = MemoryRepository(tmp_path / "memory.db", embedder=embedder)
    doc = tmp_path / "prefisso.md"
    doc.write_text("contenuto da indicizzare con prefisso passage.", encoding="utf-8")
    repository.upsert_document(doc, "docs/prefisso.md", chunk_text(doc.read_text()))

    assert embedder.embed_documents_calls, "upsert must call embed_documents"
    assert not embedder.embed_calls, "upsert must NOT call the generic embed()"
    flattened = [text for call in embedder.embed_documents_calls for text in call]
    assert all("prefisso passage" in text for text in flattened)


def test_dry_run_uses_name_only_embedder(tmp_path: Path) -> None:
    """Warning-5: the dry-run path resolves the persisted model name without
    instantiating any model (no download for two counters)."""
    from truenex_memory.core.embedder import (
        TARGET_EMBEDDING_MODEL,
        sentence_transformers_model_name,
    )
    from truenex_memory.ingestion.reindex_embeddings import ModelNameOnlyEmbedder

    _repository_with_chunks(tmp_path, doc_count=1)
    name_only = ModelNameOnlyEmbedder(sentence_transformers_model_name())
    assert name_only.model_name == f"sentence-transformers:{TARGET_EMBEDDING_MODEL}"

    report = reindex_embeddings(tmp_path / "memory.db", embedder=name_only, dry_run=True)
    assert report.dry_run
    assert report.to_reindex == report.total_chunks > 0
    assert report.processed == 0

    import pytest

    with pytest.raises(RuntimeError, match="dry-run"):
        name_only.embed_documents(["x"])

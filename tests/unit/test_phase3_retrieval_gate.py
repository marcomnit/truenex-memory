"""Tests for Phase 3.1 retrieval gate: ledger-aware chunk filtering.

Covers both the lexical fallback path (_search_chunks) and the sqlite-vector
fallback path (_sqlite_vector_matches inside _search_semantic_chunks).
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.sqlite import connect, initialize_schema
from truenex_memory.store.source_ledger import upsert_ledger_entry


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"task_work_phase3_retrieval_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


class _StubEmbedder:
    """Embedder that returns a constant non-zero vector so every chunk matches."""

    model_name = "stub"

    def embed(self, text: str) -> list[float]:  # noqa: ARG002
        return [1.0] * 32


# ---------------------------------------------------------------------------
# Lexical path: MemoryRepository without embedder (no semantic path)
# ---------------------------------------------------------------------------


class TestRetrievalGateLexical:
    """_search_chunks must exclude chunks with missing/skipped ledger rows."""

    def test_active_ledger_chunks_retrievable(self) -> None:
        wd = _workdir("lexical_active")
        repo = MemoryRepository(wd / "memory.db")
        doc_path = wd / "readme.md"
        content = "# Project\n\nSQLite is the metadata store.\n"
        doc_path.write_text(content, encoding="utf-8")
        repo.upsert_document(doc_path, str(doc_path), chunk_text(content))

        with connect(repo.db_path) as conn:
            initialize_schema(conn)
            upsert_ledger_entry(
                conn, "src_active", str(doc_path), "project_docs", status="active",
            )

        results = repo.search("SQLite metadata", top_k=5)
        assert len(results) >= 1
        assert any("SQLite" in r.content for r in results)

    def test_missing_ledger_chunks_not_retrievable(self) -> None:
        wd = _workdir("lexical_missing")
        repo = MemoryRepository(wd / "memory.db")
        doc_path = wd / "readme.md"
        content = "# Project\n\nSQLite is the metadata store.\n"
        doc_path.write_text(content, encoding="utf-8")
        repo.upsert_document(doc_path, str(doc_path), chunk_text(content))

        with connect(repo.db_path) as conn:
            initialize_schema(conn)
            upsert_ledger_entry(
                conn, "src_missing", str(doc_path), "project_docs", status="missing",
            )

        results = repo.search("SQLite metadata", top_k=5)
        assert all("SQLite" not in r.content for r in results)

    def test_skipped_ledger_chunks_not_retrievable(self) -> None:
        wd = _workdir("lexical_skipped")
        repo = MemoryRepository(wd / "memory.db")
        doc_path = wd / "readme.md"
        content = "# Project\n\nSQLite is the metadata store.\n"
        doc_path.write_text(content, encoding="utf-8")
        repo.upsert_document(doc_path, str(doc_path), chunk_text(content))

        with connect(repo.db_path) as conn:
            initialize_schema(conn)
            upsert_ledger_entry(
                conn, "src_skipped", str(doc_path), "project_docs", status="skipped",
            )

        results = repo.search("SQLite metadata", top_k=5)
        assert all("SQLite" not in r.content for r in results)

    def test_no_ledger_row_chunks_still_retrievable(self) -> None:
        """Backward compat: chunks without any source_ledger row remain visible."""
        wd = _workdir("lexical_no_ledger")
        repo = MemoryRepository(wd / "memory.db")
        doc_path = wd / "readme.md"
        content = "# Project\n\nSQLite is the metadata store.\n"
        doc_path.write_text(content, encoding="utf-8")
        repo.upsert_document(doc_path, str(doc_path), chunk_text(content))

        # No ledger entry at all.
        results = repo.search("SQLite metadata", top_k=5)
        assert len(results) >= 1
        assert any("SQLite" in r.content for r in results)

    def test_mixed_ledger_statuses_partial_results(self) -> None:
        wd = _workdir("lexical_mixed")
        repo = MemoryRepository(wd / "memory.db")

        # active document
        doc_a = wd / "active.md"
        doc_a.write_text("# Active\n\nActive document text.\n", encoding="utf-8")
        repo.upsert_document(doc_a, str(doc_a), chunk_text(doc_a.read_text(encoding="utf-8")))
        with connect(repo.db_path) as conn:
            upsert_ledger_entry(conn, "src_a", str(doc_a), "project_docs", status="active")

        # missing document
        doc_m = wd / "missing.md"
        doc_m.write_text("# Missing\n\nMissing document text.\n", encoding="utf-8")
        repo.upsert_document(doc_m, str(doc_m), chunk_text(doc_m.read_text(encoding="utf-8")))
        with connect(repo.db_path) as conn:
            upsert_ledger_entry(conn, "src_m", str(doc_m), "project_docs", status="missing")

        results = repo.search("document text", top_k=10)
        assert any("Active" in r.content for r in results)
        assert all("Missing" not in r.content for r in results)


# ---------------------------------------------------------------------------
# SQLite-vector fallback path: embedder present, vector_store absent
# ---------------------------------------------------------------------------


class TestRetrievalGateSqliteVector:
    """_sqlite_vector_matches must exclude chunks with missing/skipped ledger rows."""

    def test_active_ledger_chunks_retrievable(self) -> None:
        wd = _workdir("vector_active")
        repo = MemoryRepository(wd / "memory.db", embedder=_StubEmbedder())
        doc_path = wd / "readme.md"
        content = "# Project\n\nQdrant is the planned vector store.\n"
        doc_path.write_text(content, encoding="utf-8")
        repo.upsert_document(doc_path, str(doc_path), chunk_text(content))

        with connect(repo.db_path) as conn:
            upsert_ledger_entry(
                conn, "src_active_vec", str(doc_path), "project_docs", status="active",
            )

        results = repo.search("vector store", top_k=5)
        assert len(results) >= 1

    def test_missing_ledger_chunks_not_retrievable(self) -> None:
        wd = _workdir("vector_missing")
        repo = MemoryRepository(wd / "memory.db", embedder=_StubEmbedder())
        doc_path = wd / "readme.md"
        content = "# Project\n\nQdrant is the planned vector store.\n"
        doc_path.write_text(content, encoding="utf-8")
        repo.upsert_document(doc_path, str(doc_path), chunk_text(content))

        with connect(repo.db_path) as conn:
            upsert_ledger_entry(
                conn, "src_missing_vec", str(doc_path), "project_docs", status="missing",
            )

        results = repo.search("vector store", top_k=5)
        assert all("Qdrant" not in r.content for r in results)

    def test_skipped_ledger_chunks_not_retrievable(self) -> None:
        wd = _workdir("vector_skipped")
        repo = MemoryRepository(wd / "memory.db", embedder=_StubEmbedder())
        doc_path = wd / "readme.md"
        content = "# Project\n\nQdrant is the planned vector store.\n"
        doc_path.write_text(content, encoding="utf-8")
        repo.upsert_document(doc_path, str(doc_path), chunk_text(content))

        with connect(repo.db_path) as conn:
            upsert_ledger_entry(
                conn, "src_skipped_vec", str(doc_path), "project_docs", status="skipped",
            )

        results = repo.search("vector store", top_k=5)
        assert all("Qdrant" not in r.content for r in results)

    def test_no_ledger_row_chunks_still_retrievable(self) -> None:
        wd = _workdir("vector_no_ledger")
        repo = MemoryRepository(wd / "memory.db", embedder=_StubEmbedder())
        doc_path = wd / "readme.md"
        content = "# Project\n\nQdrant is the planned vector store.\n"
        doc_path.write_text(content, encoding="utf-8")
        repo.upsert_document(doc_path, str(doc_path), chunk_text(content))

        # No ledger entry at all: backward compat.
        results = repo.search("vector store", top_k=5)
        assert len(results) >= 1

    def test_skipped_jsonl_without_active_version_blocked(self) -> None:
        """Skipped JSONL with no previous active version is not retrievable."""
        wd = _workdir("vector_skipped_jsonl")
        repo = MemoryRepository(wd / "memory.db", embedder=_StubEmbedder())
        doc_path = wd / "session.jsonl"
        content = (
            '{"type":"user","message":{"role":"user","content":"Hello"}}\n'
        )
        doc_path.write_text(content, encoding="utf-8")
        repo.upsert_document(doc_path, str(doc_path), chunk_text(content))

        with connect(repo.db_path) as conn:
            upsert_ledger_entry(
                conn, "src_skipped_jsonl", str(doc_path), "agent_session",
                status="skipped",
                error_message="JSONL modified recently, not yet stable",
            )

        results = repo.search("Hello", top_k=5)
        # Chunk was written but ledger says skipped (never active).
        assert all("Hello" not in r.content for r in results)

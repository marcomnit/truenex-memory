"""Tests for semantic retrieval behavior."""

import shutil
import uuid
from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.retrieval.semantic import HashingEmbedder, VectorMatch, VectorPoint
from truenex_memory.store.repository import MemoryRepository


class StaticVectorStore:
    def __init__(self) -> None:
        self.points: list[VectorPoint] = []
        self.unavailable = False

    def upsert(self, points: list[VectorPoint]) -> None:
        self.points = list(points)

    def search(self, vector: list[float], *, top_k: int) -> list[VectorMatch]:
        if self.unavailable:
            raise RuntimeError("vector store unavailable")
        if not self.points:
            return []
        return [VectorMatch(point_id=self.points[0].point_id, score=0.8123)]


class EmptyVectorStore(StaticVectorStore):
    def search(self, vector: list[float], *, top_k: int) -> list[VectorMatch]:
        return []


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / f"{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def test_search_uses_vector_results_with_source_heading_and_score() -> None:
    workdir = _workdir("semantic_search")
    doc_path = workdir / "runtime.md"
    doc_path.write_text("# Runtime\n\nUse Redis for cache warmups.", encoding="utf-8")
    vector_store = StaticVectorStore()
    repository = MemoryRepository(
        workdir / "memory.db",
        embedder=HashingEmbedder(),
        vector_store=vector_store,
    )
    repository.upsert_document(doc_path, "docs/runtime.md", chunk_text(doc_path.read_text()))

    results = repository.search("terms absent from document", top_k=3)

    assert len(results) == 1
    assert results[0].source_path == "docs/runtime.md"
    assert results[0].heading_path == "Runtime"
    assert results[0].score == 0.8123
    assert results[0].memory_type == "document_chunk"


def test_text_fallback_remains_active_when_vector_store_is_empty_or_unavailable() -> None:
    workdir = _workdir("semantic_fallback")
    empty_repository = MemoryRepository(
        workdir / "empty.db",
        embedder=HashingEmbedder(),
        vector_store=EmptyVectorStore(),
    )
    empty_repository.add_memory("SQLite fallback search remains available.", memory_type="decision")

    empty_results = empty_repository.search("SQLite fallback")

    unavailable_store = StaticVectorStore()
    unavailable_store.unavailable = True
    unavailable_repository = MemoryRepository(
        workdir / "unavailable.db",
        embedder=HashingEmbedder(),
        vector_store=unavailable_store,
    )
    unavailable_repository.add_memory("Local text retrieval handles vector outages.")

    unavailable_results = unavailable_repository.search("vector outages")

    assert empty_results
    assert empty_results[0].memory_type == "decision"
    assert unavailable_results
    assert "vector outages" in unavailable_results[0].content


def test_obsolete_memories_are_excluded_by_default_with_semantic_retrieval() -> None:
    workdir = _workdir("semantic_obsolete")
    repository = MemoryRepository(
        workdir / "memory.db",
        embedder=HashingEmbedder(),
        vector_store=EmptyVectorStore(),
    )
    repository.add_memory("PostgreSQL is obsolete local metadata guidance.", status="obsolete")
    repository.add_memory("SQLite is current local metadata guidance.", status="active")

    results = repository.search("local metadata guidance")

    assert results
    assert all(result.status != "obsolete" for result in results)
    assert all("PostgreSQL" not in result.content for result in results)

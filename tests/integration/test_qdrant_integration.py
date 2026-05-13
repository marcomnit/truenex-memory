"""Optional Qdrant integration tests."""

from __future__ import annotations

import os
from pathlib import Path
import uuid

import pytest

from truenex_memory.core.chunker import chunk_text
from truenex_memory.retrieval.semantic import HashingEmbedder
from truenex_memory.store.qdrant_store import QdrantVectorStore, VectorStoreUnavailable
from truenex_memory.store.repository import MemoryRepository


def _workdir(name: str) -> Path:
    path = Path("tests/integration/.task_work") / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


@pytest.mark.qdrant
def test_qdrant_vector_store_end_to_end() -> None:
    pytest.importorskip("qdrant_client")
    embedder = HashingEmbedder()
    collection = f"truenex_memory_test_{uuid.uuid4().hex}"
    qdrant_url = os.getenv("TRUENEX_MEMORY_QDRANT_URL", "http://localhost:6333")
    try:
        vector_store = QdrantVectorStore(
            collection_name=collection,
            dimensions=embedder.dimensions,
            url=qdrant_url,
        )
        vector_store.initialize()
    except VectorStoreUnavailable as exc:
        pytest.skip(f"Qdrant is not reachable: {exc}")

    workdir = _workdir("qdrant_e2e")
    doc = workdir / "architecture.md"
    doc.write_text("# Vector Search\n\nQdrant stores semantic vectors locally.", encoding="utf-8")
    repository = MemoryRepository(
        workdir / "memory.db",
        embedder=embedder,
        vector_store=vector_store,
    )
    repository.upsert_document(doc, "architecture.md", chunk_text(doc.read_text(encoding="utf-8")))

    results = repository.search("semantic vectors", top_k=3)

    assert results
    assert results[0].source_path == "architecture.md"
    assert results[0].score > 0

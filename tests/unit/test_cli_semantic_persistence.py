"""Tests for default semantic retrieval across service instances."""

import shutil
import uuid
from pathlib import Path

from truenex_memory.core.memory_service import MemoryService
from truenex_memory.store.sqlite import connect


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / f"{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def test_service_persists_vectors_for_search_in_later_instance() -> None:
    workdir = _workdir("semantic_service")
    doc_path = workdir / "architecture.md"
    doc_path.write_text("# Storage\n\nSQLite stores local metadata and retrieval logs.", encoding="utf-8")

    indexing_service = MemoryService(workdir)
    assert indexing_service.index(doc_path) == 1

    search_service = MemoryService(workdir)
    results = search_service.search("retrieval logs", top_k=1)

    assert results
    assert results[0].source_path == "architecture.md"
    assert results[0].heading_path == "Storage"
    assert results[0].score > 0


def test_index_records_embedding_metadata_in_sqlite() -> None:
    workdir = _workdir("semantic_metadata")
    doc_path = workdir / "decision.md"
    doc_path.write_text("# Decision\n\nUse Qdrant for vector search when available.", encoding="utf-8")

    service = MemoryService(workdir)
    service.index(doc_path)

    with connect(service.config.db_path) as conn:
        row = conn.execute(
            "SELECT qdrant_point_id, embedding_model, embedding_vector_json FROM chunks"
        ).fetchone()

    assert str(uuid.UUID(row["qdrant_point_id"])) == row["qdrant_point_id"]
    assert row["embedding_model"] == "hashing-fallback:intfloat/multilingual-e5-base"
    assert row["embedding_vector_json"].startswith("[")

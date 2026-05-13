"""Tests for semantic metadata written during indexing."""

import shutil
import sqlite3
import uuid
from pathlib import Path

from truenex_memory.core.indexer import index_path
from truenex_memory.retrieval.semantic import HashingEmbedder, VectorMatch, VectorPoint
from truenex_memory.store.repository import MemoryRepository


class RecordingVectorStore:
    def __init__(self) -> None:
        self.points: list[VectorPoint] = []

    def upsert(self, points: list[VectorPoint]) -> None:
        self.points.extend(points)

    def search(self, vector: list[float], *, top_k: int) -> list[VectorMatch]:
        return []


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / f"{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def test_indexing_saves_vector_point_id_for_chunks() -> None:
    workdir = _workdir("semantic_index")
    doc_path = workdir / "architecture.md"
    doc_path.write_text("# Runtime\n\nUse SQLite for local metadata.", encoding="utf-8")
    vector_store = RecordingVectorStore()
    repository = MemoryRepository(
        workdir / "memory.db",
        embedder=HashingEmbedder(),
        vector_store=vector_store,
    )

    indexed = index_path(doc_path, project_root=workdir, repository=repository)

    assert indexed == 1
    assert len(vector_store.points) == 1
    with sqlite3.connect(workdir / "memory.db") as conn:
        row = conn.execute(
            "SELECT qdrant_point_id, heading_path FROM chunks"
        ).fetchone()
    assert row == (vector_store.points[0].point_id, "Runtime")

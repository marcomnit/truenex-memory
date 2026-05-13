"""Tests for optional vector stores."""

import sys

import pytest

from truenex_memory.retrieval.semantic import VectorPoint as SemanticVectorPoint
from truenex_memory.store.qdrant_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorPoint,
    VectorStoreUnavailable,
)


def test_in_memory_vector_store_upserts_searches_and_deletes() -> None:
    store = InMemoryVectorStore(dimensions=3)
    store.upsert(
        [
            VectorPoint(id="a", vector=[1.0, 0.0, 0.0], payload={"text": "alpha"}),
            VectorPoint(id="b", vector=[0.0, 1.0, 0.0], payload={"text": "beta"}),
        ]
    )

    hits = store.search([0.9, 0.1, 0.0], limit=2)

    assert [hit.id for hit in hits] == ["a", "b"]
    assert hits[0].payload == {"text": "alpha"}
    store.delete(["a"])
    assert store.count() == 1


def test_in_memory_vector_store_accepts_repository_protocol() -> None:
    store = InMemoryVectorStore(dimensions=3)

    store.upsert(
        [
            SemanticVectorPoint(
                point_id="repo-a",
                vector=[1.0, 0.0, 0.0],
                payload={"chunk_id": "chunk-a"},
            )
        ]
    )

    hits = store.search([1.0, 0.0, 0.0], top_k=1)

    assert hits[0].id == "repo-a"
    assert hits[0].payload == {"chunk_id": "chunk-a"}


def test_in_memory_vector_store_validates_dimensions_and_limit() -> None:
    store = InMemoryVectorStore(dimensions=2)

    with pytest.raises(ValueError, match="dimensions"):
        store.upsert([VectorPoint(id="bad", vector=[1.0])])

    with pytest.raises(ValueError, match="limit"):
        store.search([1.0, 0.0], limit=0)


def test_qdrant_store_reports_missing_optional_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "qdrant_client", None)

    with pytest.raises(VectorStoreUnavailable, match="qdrant-client"):
        QdrantVectorStore(collection_name="memory", dimensions=3, url="http://127.0.0.1:6333")


def test_qdrant_store_wraps_unreachable_client() -> None:
    class FailingClient:
        def get_collection(self, collection_name: str) -> None:
            raise RuntimeError("offline")

        def create_collection(self, **kwargs: object) -> None:
            raise RuntimeError("offline")

    pytest.importorskip("qdrant_client")

    store = QdrantVectorStore(collection_name="memory", dimensions=3, client=FailingClient())

    with pytest.raises(VectorStoreUnavailable, match="Qdrant is not reachable"):
        store.initialize()

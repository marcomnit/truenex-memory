"""Local semantic retrieval primitives."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import uuid
from typing import Protocol

from truenex_memory.core.embedder import HashingEmbedder


@dataclass(frozen=True)
class VectorPoint:
    """A chunk embedding ready for vector-store upsert."""

    point_id: str
    vector: list[float]
    payload: dict[str, object]


@dataclass(frozen=True)
class VectorMatch:
    """A vector-store match returned by semantic search."""

    point_id: str
    score: float


class Embedder(Protocol):
    """Minimal embedding interface used by local semantic retrieval."""

    @property
    def model_name(self) -> str:
        """Return the model/backend name stored with persisted vectors."""

    def embed(self, text: str) -> list[float]:
        """Return an embedding for text."""


class VectorStore(Protocol):
    """Minimal vector store interface used by the repository."""

    def upsert(self, points: list[VectorPoint]) -> None:
        """Store or replace vector points."""

    def search(self, vector: list[float], *, top_k: int) -> list[VectorMatch]:
        """Return nearest points for a query vector."""


class InMemoryVectorStore:
    """Small deterministic vector store for local tests."""

    def __init__(self) -> None:
        self.points: dict[str, VectorPoint] = {}

    def upsert(self, points: list[VectorPoint]) -> None:
        for point in points:
            self.points[point.point_id] = point

    def search(self, vector: list[float], *, top_k: int) -> list[VectorMatch]:
        if top_k < 1:
            raise ValueError("top_k must be greater than zero")
        matches = [
            VectorMatch(point_id=point.point_id, score=round(_cosine(vector, point.vector), 4))
            for point in self.points.values()
        ]
        matches = [match for match in matches if match.score > 0]
        matches.sort(key=lambda item: item.score, reverse=True)
        return matches[:top_k]


def chunk_point_id(chunk_id: str) -> str:
    """Return a stable Qdrant-compatible point id for an indexed chunk."""

    digest = hashlib.sha256(chunk_id.encode("utf-8")).hexdigest()[:32]
    return str(uuid.UUID(hex=digest))


def _normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))

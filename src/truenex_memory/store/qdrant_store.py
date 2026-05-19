"""Optional Qdrant adapter and local in-memory vector store."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

Payload = dict[str, Any]


class VectorStoreUnavailable(RuntimeError):
    """Raised when an optional vector backend cannot be used."""


@dataclass(frozen=True)
class VectorPoint:
    """A vector and payload ready to be stored."""

    id: str
    vector: list[float]
    payload: Payload = field(default_factory=dict)


@dataclass(frozen=True)
class VectorSearchHit:
    """A ranked vector retrieval result."""

    id: str
    score: float
    payload: Payload


class InMemoryVectorStore:
    """Small cosine-similarity store for unit tests and local fallback."""

    def __init__(self, dimensions: int) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be greater than zero")
        self.dimensions = dimensions
        self._points: dict[str, VectorPoint] = {}

    def upsert(self, points: list[object]) -> None:
        for point in points:
            vector = _point_vector(point)
            point_id = _point_id(point)
            payload = _point_payload(point)
            self._validate_vector(vector)
            self._points[point_id] = VectorPoint(
                id=point_id,
                vector=list(vector),
                payload=dict(payload),
            )

    def search(
        self,
        vector: list[float],
        *,
        limit: int | None = None,
        top_k: int | None = None,
    ) -> list[VectorSearchHit]:
        limit = _resolve_limit(limit=limit, top_k=top_k)
        self._validate_limit(limit)
        self._validate_vector(vector)
        hits = [
            VectorSearchHit(id=point.id, score=_cosine(vector, point.vector), payload=dict(point.payload))
            for point in self._points.values()
        ]
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def delete(self, ids: list[str]) -> None:
        for point_id in ids:
            self._points.pop(point_id, None)

    def count(self) -> int:
        return len(self._points)

    def _validate_vector(self, vector: list[float]) -> None:
        if len(vector) != self.dimensions:
            raise ValueError(f"vector must have {self.dimensions} dimensions")

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be greater than zero")


class QdrantVectorStore:
    """Thin adapter around ``qdrant-client`` with controlled failure modes."""

    def __init__(
        self,
        *,
        collection_name: str,
        dimensions: int,
        url: str | None = None,
        client: Any | None = None,
        distance: str = "Cosine",
        timeout: float = 5.0,
        retries: int = 2,
    ) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be greater than zero")
        if not collection_name.strip():
            raise ValueError("collection_name cannot be empty")
        self.collection_name = collection_name
        self.dimensions = dimensions
        self.distance = distance
        self._timeout = timeout
        self._retries = retries
        self._client = client if client is not None else self._build_client(url, timeout=self._timeout)
        self._models = _load_qdrant_models()

    def initialize(self) -> None:
        def _do() -> None:
            if self._collection_exists():
                return
            distance = getattr(self._models.Distance, self.distance.upper())
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=self._models.VectorParams(size=self.dimensions, distance=distance),
            )
        self._with_retry(_do, "initialize")

    def upsert(self, points: list[object]) -> None:
        self.initialize()
        def _do() -> None:
            qdrant_points = [
                self._models.PointStruct(
                    id=_point_id(point),
                    vector=_point_vector(point),
                    payload=dict(_point_payload(point)),
                )
                for point in points
            ]
            self._client.upsert(collection_name=self.collection_name, points=qdrant_points)
        self._with_retry(_do, "upsert")

    def search(
        self,
        vector: list[float],
        *,
        limit: int | None = None,
        top_k: int | None = None,
    ) -> list[VectorSearchHit]:
        limit = _resolve_limit(limit=limit, top_k=top_k)
        InMemoryVectorStore._validate_limit(limit)
        self.initialize()
        def _do() -> list[VectorSearchHit]:
            rows = self._client.search(
                collection_name=self.collection_name,
                query_vector=vector,
                limit=limit,
            )
            return [
                VectorSearchHit(id=str(row.id), score=float(row.score), payload=dict(row.payload or {}))
                for row in rows
            ]
        return self._with_retry(_do, "search")

    def delete(self, ids: list[str]) -> None:
        self.initialize()
        def _do() -> None:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=self._models.PointIdsList(points=ids),
            )
        self._with_retry(_do, "delete")

    def _collection_exists(self) -> bool:
        try:
            self._client.get_collection(self.collection_name)
            return True
        except Exception:
            return False

    def _with_retry(self, fn: Callable[[], Any], operation: str) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                return fn()
            except VectorStoreUnavailable:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning("Qdrant %s failed (attempt %d/%d): %s", operation, attempt, self._retries, exc)
                if attempt < self._retries:
                    time.sleep(0.5 * attempt)
        raise VectorStoreUnavailable(f"Qdrant {operation} failed after {self._retries} attempts: {last_exc}") from last_exc

    @staticmethod
    def _build_client(url: str | None, timeout: float = 5.0) -> Any:
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise VectorStoreUnavailable("qdrant-client is not installed") from exc
        try:
            return QdrantClient(url=url, timeout=timeout) if url else QdrantClient(":memory:")
        except Exception as exc:  # pragma: no cover - depends on client version
            raise VectorStoreUnavailable(f"Qdrant client could not be created: {exc}") from exc


def _load_qdrant_models() -> Any:
    try:
        from qdrant_client import models
    except ImportError as exc:
        raise VectorStoreUnavailable("qdrant-client is not installed") from exc
    return models


def _resolve_limit(*, limit: int | None, top_k: int | None) -> int:
    return top_k if top_k is not None else (limit if limit is not None else 5)


def _point_id(point: object) -> str:
    point_id = getattr(point, "id", None) or getattr(point, "point_id", None)
    if point_id is None:
        raise ValueError("vector point is missing an id")
    return str(point_id)


def _point_vector(point: object) -> list[float]:
    vector = getattr(point, "vector", None)
    if vector is None:
        raise ValueError("vector point is missing a vector")
    return [float(value) for value in vector]


def _point_payload(point: object) -> Payload:
    payload = getattr(point, "payload", None)
    if payload is None:
        return {}
    return dict(payload)


def _cosine(left: list[float], right: list[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    dot = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
    return dot / (left_norm * right_norm)

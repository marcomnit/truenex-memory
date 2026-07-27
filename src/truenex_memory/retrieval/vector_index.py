"""In-process dense vector index backed by numpy.

A per-process cache keyed ``(db_path, embedding_model)`` holds the model's
chunk vectors as a single float32 matrix, so dense retrieval is one BLAS
matrix-vector product instead of a Python loop over hundreds of thousands
of JSON rows (measured: the Python full-scan does not scale to ~478k x
768d).

Invalidation criterion (cheap and robust): the cache entry is keyed by
``MAX(updated_at)`` of the model's chunks, re-checked per search with ONE
query served by the covering index ``idx_chunks_embedding_model`` as an
index range-end seek (~ms). It changes exactly when the model's chunk set
changes: document re-indexing (``upsert_document`` bumps ``updated_at``)
and re-embedding (``reindex_embeddings`` bumps it on every migrated
chunk). An exact ``COUNT(*)`` was rejected: on ~85k embedded rows it
scans the whole index range (~1s per query, measured). The DB file
``st_mtime`` is NOT usable either: ``search()`` itself writes
``retrieval_logs`` on every call, so mtime invalidates after every query
(measured: full rebuild per search).

Operational note: reindex_embeddings bumps ``updated_at`` on every
committed batch, so each batch invalidates this cache. With the dense
ranker active, any search during a reindex window pays a full matrix
reload (~45s per query on the live store, measured) — run the reindex in
a quiet window or with TRUENEX_DENSE=off.

RAM: N vectors x D dims x 4 bytes (float32). On the live store (~478k x
768d) that is ~1.5 GB; the allocated megabytes are logged after load. If
numpy is not importable, callers must fall back to the legacy Python scan
(``_sqlite_vector_matches``) — a one-time warning is logged here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from truenex_memory.retrieval.semantic import VectorMatch

logger = logging.getLogger(__name__)

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    np = None  # type: ignore[assignment]
    NUMPY_AVAILABLE = False

_LOAD_BATCH = 10_000


@dataclass
class VectorIndexEntry:
    """Cached matrix for one (db_path, embedding_model) pair."""

    vector_count: int
    max_updated_at: str | None
    point_ids: list[str]
    matrix: "np.ndarray"  # float32, shape (N, D), rows L2-normalized


# (db_path, embedding_model) -> entry
_CACHE: dict[tuple[str, str], VectorIndexEntry] = {}
_WARNED_NO_NUMPY = False


def numpy_available() -> bool:
    return NUMPY_AVAILABLE


def clear_cache() -> None:
    """Drop every cached index (tests and explicit invalidation)."""

    _CACHE.clear()


def _model_max_updated(conn: sqlite3.Connection, embedding_model: str) -> str | None:
    # MAX(updated_at) is served by the covering index
    # idx_chunks_embedding_model (embedding_model, updated_at) via the
    # min/max optimization as a range-end seek (~0.1 ms on ~85k embedded
    # rows, measured). COUNT(*) was rejected: it scans the whole index
    # range (~1s/query, measured). Reindex and upsert both bump
    # updated_at, so MAX alone detects every mutation of the model's
    # chunk set. NULL means no embedded chunks.
    row = conn.execute(
        "SELECT MAX(updated_at) FROM chunks WHERE embedding_model = ?",
        (embedding_model,),
    ).fetchone()
    return row[0]


def get_index(
    db_path: Path,
    conn: sqlite3.Connection,
    embedding_model: str,
) -> VectorIndexEntry | None:
    """Return the cached index for the model, building/rebuilding as needed.

    Returns None when numpy is unavailable (caller falls back to the legacy
    Python scan) or when the model has no vectors at all.
    """

    global _WARNED_NO_NUMPY
    if not NUMPY_AVAILABLE:
        if not _WARNED_NO_NUMPY:
            logger.warning(
                "numpy is not importable: dense retrieval falls back to the "
                "legacy Python vector scan (slow on large stores)"
            )
            _WARNED_NO_NUMPY = True
        return None

    key = (str(db_path), embedding_model)
    max_updated = _model_max_updated(conn, embedding_model)
    if max_updated is None:
        _CACHE.pop(key, None)
        return None

    cached = _CACHE.get(key)
    if cached is not None and cached.max_updated_at == max_updated:
        return cached

    entry = _build_index(conn, embedding_model, max_updated)
    if entry is not None:
        _CACHE[key] = entry
    return entry


def _build_index(
    conn: sqlite3.Connection,
    embedding_model: str,
    max_updated: str,
) -> VectorIndexEntry | None:
    point_ids: list[str] = []
    vectors: list[list[float]] = []
    cursor = conn.execute(
        "SELECT qdrant_point_id, embedding_vector_json FROM chunks "
        "WHERE embedding_model = ? AND qdrant_point_id IS NOT NULL "
        "AND embedding_vector_json IS NOT NULL",
        (embedding_model,),
    )
    while True:
        rows = cursor.fetchmany(_LOAD_BATCH)
        if not rows:
            break
        for point_id, vector_json in rows:
            try:
                vector = json.loads(vector_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(vector, list):
                continue
            point_ids.append(str(point_id))
            vectors.append([float(value) for value in vector])
    if not point_ids:
        return None

    matrix = np.asarray(vectors, dtype=np.float32)
    # L2-normalize rows so matrix @ query IS the cosine similarity (e5
    # vectors are already normalized, this is a cheap safety net).
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.divide(matrix, norms, out=matrix, where=norms > 0)
    megabytes = matrix.nbytes / (1024 * 1024)
    logger.info(
        "dense vector index loaded: model=%s vectors=%d dims=%d ram=%.0fMB",
        embedding_model,
        matrix.shape[0],
        matrix.shape[1],
        megabytes,
    )
    return VectorIndexEntry(
        vector_count=len(point_ids),
        max_updated_at=max_updated,
        point_ids=point_ids,
        matrix=matrix,
    )


def search_index(
    entry: VectorIndexEntry,
    query_vector: list[float],
    top_k: int,
) -> list[VectorMatch]:
    """Cosine top-k against the cached matrix via one BLAS matvec."""

    if top_k < 1:
        raise ValueError("top_k must be greater than zero")
    query = np.asarray(query_vector, dtype=np.float32)
    if query.shape[0] != entry.matrix.shape[1]:
        # Dimension mismatch (e.g. stale entry for another model): the
        # cosine must never be computed across dimensions.
        return []
    norm = float(np.linalg.norm(query))
    if norm > 0:
        query = query / norm
    scores = entry.matrix @ query
    k = min(top_k, scores.shape[0])
    # argpartition is O(N); only the top-k slice is fully sorted.
    candidates = np.argpartition(-scores, k - 1)[:k]
    ordered = candidates[np.argsort(-scores[candidates])]
    matches: list[VectorMatch] = []
    for index in ordered:
        score = float(scores[index])
        if score <= 0:
            continue
        matches.append(
            VectorMatch(point_id=entry.point_ids[int(index)], score=round(score, 4))
        )
    return matches

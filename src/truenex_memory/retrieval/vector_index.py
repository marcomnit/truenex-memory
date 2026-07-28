"""In-process dense vector index backed by numpy, with persistent memmap cache.

A per-process cache keyed ``(db_path, embedding_model)`` holds the model's
chunk vectors as a single float32 matrix, so dense retrieval is one BLAS
matrix-vector product instead of a Python loop over hundreds of thousands
of JSON rows (measured: the Python full-scan does not scale to ~478k x
768d).

The index is built LAZILY on the first dense search of the process (the
only caller is ``MemoryRepository._search_semantic_chunks``) — never at
MCP server startup. To make that first build effectively free, the matrix
is persisted under ``<db_parent>/vector_cache/<sanitized_model>.npy`` (+
``.json`` sidecar with the point_id mapping and the validity stamp): the
next process opens it with ``np.load(mmap_mode="r")`` instead of
re-parsing ~478k JSON rows from SQLite (~150s measured). The memmap open
itself measures ~0.1s; on top of that the sidecar JSON parse (~19 MB,
478k point_ids) costs an estimated 100-300 ms once per process, and the
matrix pages are read on demand (first matvec pages the matrix in
through the OS page cache; process RSS stays low until then). The
sidecar is valid only when its ``max_updated_at``
equals the current ``MAX(updated_at)`` of the model's chunks; anything
else triggers a full rebuild + atomic cache rewrite (temp file +
``os.replace``). A reader holding an old memmap can make the replace fail
on Windows: the write is best-effort, a warning is logged, and the slow
build result is still returned — cache failures never crash search.

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
(measured: full rebuild per search). Known limitation (inherited by the
persistent cache): deleting a chunk that does NOT hold the max
``updated_at`` leaves the stamp unchanged, so a stale point_id may
survive — hydration simply skips the missing row, never crashes.

Operational note: reindex_embeddings bumps ``updated_at`` on every
committed batch, so each batch invalidates this cache. With the dense
ranker active, any search during a reindex window pays a full matrix
reload (~45s per query on the live store, measured) — run the reindex in
a quiet window or with TRUENEX_DENSE=off.

RAM: N vectors x D dims x 4 bytes (float32). On the live store (~478k x
768d) that is ~1.5 GB; the allocated megabytes are logged after a slow
build. If numpy is not importable, callers must fall back to the legacy
Python scan (``_sqlite_vector_matches``) — a one-time warning is logged
here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
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
_CACHE_DIR_NAME = "vector_cache"


@dataclass
class VectorIndexEntry:
    """Cached matrix for one (db_path, embedding_model) pair."""

    vector_count: int
    max_updated_at: str | None
    point_ids: list[str]
    matrix: "np.ndarray"  # float32, shape (N, D), rows L2-normalized
    from_memmap: bool = False  # True when backed by the persistent npy cache


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


def _sanitize_model_name(embedding_model: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", embedding_model)


def _persistent_cache_paths(db_path: Path, embedding_model: str) -> tuple[Path, Path]:
    """``(<npy matrix>, <json sidecar>)`` for the model's persistent cache."""

    cache_dir = Path(db_path).parent / _CACHE_DIR_NAME
    safe = _sanitize_model_name(embedding_model)
    return cache_dir / f"{safe}.npy", cache_dir / f"{safe}.json"


def _load_persistent_cache(
    db_path: Path, embedding_model: str, max_updated: str
) -> VectorIndexEntry | None:
    """Open the persisted matrix as a read-only memmap when the sidecar is
    valid (same model, same MAX(updated_at) stamp, consistent shape).

    Returns None on a stale/missing sidecar (silent — a rebuild follows)
    and on ANY read error (warning — cache failures must never crash
    search; the caller falls back to the slow build).
    """

    matrix_path, sidecar_path = _persistent_cache_paths(db_path, embedding_model)
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if sidecar.get("embedding_model") != embedding_model:
            return None
        if sidecar.get("max_updated_at") != max_updated:
            logger.info(
                "persistent vector cache stale for %s (sidecar stamp %r, current %r): rebuilding",
                embedding_model,
                sidecar.get("max_updated_at"),
                max_updated,
            )
            return None
        point_ids = [str(point_id) for point_id in sidecar["point_ids"]]
        vector_count = int(sidecar["vector_count"])
        dims = int(sidecar["dims"])
        if vector_count != len(point_ids) or vector_count < 1 or dims < 1:
            return None
        matrix = np.load(matrix_path, mmap_mode="r")
        if matrix.shape != (vector_count, dims):
            return None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.warning(
            "persistent vector cache unreadable for %s, falling back to slow build: %s",
            embedding_model,
            exc,
        )
        return None
    logger.info(
        "dense vector index opened from persistent memmap cache: model=%s vectors=%d dims=%d",
        embedding_model,
        vector_count,
        dims,
    )
    return VectorIndexEntry(
        vector_count=vector_count,
        max_updated_at=max_updated,
        point_ids=point_ids,
        matrix=matrix,
        from_memmap=True,
    )


def _sweep_stale_tmp_files(cache_dir: Path, *, max_age_s: float = 3600.0) -> int:
    """Best-effort GC of orphaned ``*.{pid}.tmp`` files in the cache dir.

    A writer crashed mid-save leaves its temp files behind (a matrix tmp
    is ~1.5 GB on the live store). Only files OLDER than ``max_age_s``
    (mtime) are removed: a concurrent active writer's tmp is recent, so
    the age guard prevents deleting it out from under the writer. Sweep
    failures never propagate — this runs inside the cache write path.
    """

    removed = 0
    try:
        cutoff = time.time() - max_age_s
        for tmp in cache_dir.glob("*.tmp"):
            try:
                if tmp.stat().st_mtime < cutoff:
                    tmp.unlink()
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    if removed:
        logger.info("persistent vector cache GC: removed %d stale tmp file(s)", removed)
    return removed


def _save_persistent_cache(
    db_path: Path, embedding_model: str, entry: VectorIndexEntry
) -> None:
    """Persist the built matrix + sidecar (temp file + atomic replace).

    Best-effort: a concurrent reader's memmap can make ``os.replace``
    fail on Windows; a warning is logged and the slow-build entry is
    still used — the next rebuild retries the write.
    """

    matrix_path, sidecar_path = _persistent_cache_paths(db_path, embedding_model)
    tmp_matrix = matrix_path.with_name(f"{matrix_path.name}.{os.getpid()}.tmp")
    tmp_sidecar = sidecar_path.with_name(f"{sidecar_path.name}.{os.getpid()}.tmp")
    try:
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        _sweep_stale_tmp_files(matrix_path.parent)
        with open(tmp_matrix, "wb") as handle:
            np.save(handle, np.asarray(entry.matrix, dtype=np.float32))
        sidecar = {
            "embedding_model": embedding_model,
            "vector_count": entry.vector_count,
            "dims": int(entry.matrix.shape[1]),
            "max_updated_at": entry.max_updated_at,
            "point_ids": entry.point_ids,
        }
        tmp_sidecar.write_text(json.dumps(sidecar), encoding="utf-8")
        os.replace(tmp_matrix, matrix_path)
        os.replace(tmp_sidecar, sidecar_path)
        logger.info(
            "persistent vector cache written: %s (%d vectors)",
            matrix_path,
            entry.vector_count,
        )
    except OSError as exc:
        logger.warning(
            "persistent vector cache write failed for %s (best-effort, slow build still used): %s",
            embedding_model,
            exc,
        )
        for tmp in (tmp_matrix, tmp_sidecar):
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


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
        # No vectors for this model: drop any stale persistent cache so a
        # later re-embed cannot resurrect an orphaned file.
        for path in _persistent_cache_paths(db_path, embedding_model):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return None

    cached = _CACHE.get(key)
    if cached is not None and cached.max_updated_at == max_updated:
        return cached

    entry = _load_persistent_cache(db_path, embedding_model, max_updated)
    if entry is None:
        entry = _build_index(conn, embedding_model, max_updated)
        if entry is not None:
            _save_persistent_cache(db_path, embedding_model, entry)
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

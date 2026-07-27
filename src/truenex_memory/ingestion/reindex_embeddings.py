"""Resumable batch re-embedding of indexed chunks.

Migrates ``chunks.embedding_vector_json`` / ``chunks.embedding_model`` to
the active semantic embedder (e.g. sentence-transformers e5) and backfills
``chunks.qdrant_point_id`` for chunks indexed without an embedder (NULL
point id), reusing the same ``chunk_point_id`` derivation as
``MemoryRepository.upsert_document`` — no divergent copies.

Designed to be interrupted and re-launched: keyset pagination walks chunks
by ``rowid`` (no whole-table fetchall), targets skip chunks already
carrying the active model name, and each batch is committed separately, so
a rerun simply continues where the previous one stopped.

Known limitation (same as the ledger purge): Qdrant points are NOT updated
by this job — only the SQLite rows. A Qdrant collection refresh, when the
backend is enabled, is a separate operational step.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from truenex_memory.retrieval.semantic import chunk_point_id

DEFAULT_REINDEX_BATCH_SIZE = 256
SQLITE_BUSY_TIMEOUT_MS = 30_000


def _now_iso() -> str:
    """UTC timestamp in the same ISO format as repository._now_sql."""

    return datetime.now(timezone.utc).isoformat()


class DocumentEmbedder(Protocol):
    """Minimal interface required from the active embedder."""

    @property
    def model_name(self) -> str: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class ModelNameOnlyEmbedder:
    """Dry-run stand-in exposing only ``model_name``.

    Lets the CLI count reindex targets without instantiating the real
    SentenceTransformer model (no ~1.1GB download for two counters).
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("ModelNameOnlyEmbedder cannot embed: dry-run only")


@dataclass
class ReindexReport:
    """Outcome of a reindex run (or dry-run plan)."""

    db_path: str
    model_name: str
    total_chunks: int = 0
    already_current: int = 0
    to_reindex: int = 0
    processed: int = 0
    point_ids_backfilled: int = 0
    dry_run: bool = True
    device: str | None = None
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def chunks_per_second(self) -> float:
        if self.elapsed_s <= 0 or self.processed <= 0:
            return 0.0
        return round(self.processed / self.elapsed_s, 1)

    def to_dict(self) -> dict[str, object]:
        return {
            "db_path": self.db_path,
            "model_name": self.model_name,
            "total_chunks": self.total_chunks,
            "already_current": self.already_current,
            "to_reindex": self.to_reindex,
            "processed": self.processed,
            "point_ids_backfilled": self.point_ids_backfilled,
            "dry_run": self.dry_run,
            "device": self.device,
            "elapsed_s": round(self.elapsed_s, 2),
            "chunks_per_second": self.chunks_per_second,
            "errors": self.errors,
        }


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Long-running batch job on a possibly busy database: wait for locks
    # instead of failing immediately.
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def count_reindex_targets(conn: sqlite3.Connection, model_name: str) -> tuple[int, int, int]:
    """Return (total_chunks, already_current, to_reindex) for the model."""

    total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    current = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE embedding_model = ?", (model_name,)
    ).fetchone()[0]
    return total, current, total - current


def reindex_embeddings(
    db_path: Path,
    *,
    embedder: DocumentEmbedder,
    batch_size: int = DEFAULT_REINDEX_BATCH_SIZE,
    limit: int | None = None,
    dry_run: bool = True,
    device: str | None = None,
) -> ReindexReport:
    """Re-embed chunks whose embedding_model differs from the active one.

    Resumable: keyset pagination (``rowid > last``) walks the targets
    without loading the whole table in memory, each batch is committed
    before the next one starts, and already-migrated chunks are skipped by
    the WHERE clause — interrupting and re-launching continues from the
    first not-yet-migrated chunk. ``dry_run`` only counts, never writes.
    """

    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")
    if limit is not None and limit < 1:
        raise ValueError("limit must be greater than zero")

    report = ReindexReport(
        db_path=str(db_path),
        model_name=embedder.model_name,
        dry_run=dry_run,
        device=device,
    )
    conn = _connect(db_path)
    try:
        total, current, to_reindex = count_reindex_targets(conn, embedder.model_name)
        report.total_chunks = total
        report.already_current = current
        report.to_reindex = to_reindex
        if dry_run or to_reindex == 0:
            return report

        started = time.perf_counter()
        last_rowid = 0
        remaining = limit
        while True:
            fetch_size = batch_size if remaining is None else min(batch_size, remaining)
            if fetch_size < 1:
                break
            rows = conn.execute(
                """
                SELECT rowid, id, content, qdrant_point_id FROM chunks
                WHERE (embedding_model IS NULL OR embedding_model != ?)
                  AND rowid > ?
                ORDER BY rowid
                LIMIT ?
                """,
                (embedder.model_name, last_rowid, fetch_size),
            ).fetchall()
            if not rows:
                break
            texts = [str(row["content"] or "") for row in rows]
            try:
                vectors = embedder.embed_documents(texts)
            except Exception as exc:  # keep the run resumable: report and stop
                report.errors.append(f"batch after rowid {last_rowid} failed: {exc}")
                break
            if len(vectors) != len(rows):
                report.errors.append(
                    f"embedder returned {len(vectors)} vectors for {len(rows)} texts"
                )
                break
            for row, vector in zip(rows, vectors):
                point_id = row["qdrant_point_id"] or chunk_point_id(str(row["id"]))
                if row["qdrant_point_id"] is None:
                    report.point_ids_backfilled += 1
                # updated_at bumps on every migrated chunk: it is the cheap
                # invalidation signal for the in-process vector index
                # (MAX(updated_at) is an index range-end seek, COUNT(*) a
                # full range scan). Same ISO format as _now_sql.
                conn.execute(
                    """
                    UPDATE chunks
                    SET embedding_vector_json = ?,
                        embedding_model = ?,
                        qdrant_point_id = COALESCE(qdrant_point_id, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(vector),
                        embedder.model_name,
                        point_id,
                        _now_iso(),
                        row["id"],
                    ),
                )
            conn.commit()  # per-batch commit: interruption loses at most one batch
            report.processed += len(rows)
            last_rowid = int(rows[-1]["rowid"])
            if remaining is not None:
                remaining -= len(rows)
                if remaining <= 0:
                    break
        report.elapsed_s = time.perf_counter() - started
    finally:
        conn.close()
    return report

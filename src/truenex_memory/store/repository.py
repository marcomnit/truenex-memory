"""Repository for local documents, chunks, memories and retrieval logs."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import sqlite3
import uuid

from truenex_memory.core.chunker import TextChunk, content_hash
from truenex_memory.retrieval.semantic import Embedder, VectorMatch, VectorPoint, VectorStore, chunk_point_id
from truenex_memory.store.qdrant_store import VectorSearchHit
from truenex_memory.store.models import MemoryNode, RetrievalLog, SearchHit, VALID_STATUSES
from truenex_memory.retrieval.scoring import tokenize_set
from truenex_memory.store.sqlite import connect, initialize_schema


ACTIVE_STATUSES = ("active", "unverified")
EXPORT_VERSION = "1"
PROJECT_ID = "default"
EXPORT_TABLES = ("documents", "chunks", "memory_nodes", "edges", "retrieval_logs", "schema_migrations")


class MemoryRepository:
    """SQLite-backed local repository."""

    def __init__(
        self,
        db_path: Path,
        *,
        embedder: Embedder | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.db_path = db_path
        self.embedder = embedder
        self.vector_store = vector_store
        self.last_trace_id: str | None = None

    def initialize(self) -> None:
        with connect(self.db_path) as conn:
            initialize_schema(conn)

    def add_memory(
        self,
        content: str,
        *,
        memory_type: str = "note",
        title: str | None = None,
        status: str = "active",
        source_kind: str = "manual",
        source_document_id: str | None = None,
        source_chunk_id: str | None = None,
        source_path: str | None = None,
        created_by: str = "user",
        model_name: str | None = None,
        confidence: float | None = None,
    ) -> str:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}, expected one of {sorted(VALID_STATUSES)}")
        now = _now_sql()
        memory_id = _new_id("mem")
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("memory content cannot be empty")
        self.initialize()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_nodes (
                    id, project_id, type, title, content, status, source_kind,
                    source_document_id, source_chunk_id, source_path,
                    content_hash, created_by, model_name, confidence,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    PROJECT_ID,
                    memory_type,
                    title or _title_from_content(clean_content),
                    clean_content,
                    status,
                    source_kind,
                    source_document_id,
                    source_chunk_id,
                    source_path,
                    content_hash(clean_content),
                    created_by,
                    model_name,
                    confidence,
                    now,
                    now,
                ),
            )
            conn.commit()
        return memory_id

    def find_memory_by_content_hash(self, hash_value: str) -> MemoryNode | None:
        self.initialize()
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_nodes
                WHERE project_id = ? AND content_hash = ?
                ORDER BY created_at, id
                LIMIT 1
                """,
                (PROJECT_ID, hash_value),
            ).fetchone()
            return _memory_node_from_row(row) if row is not None else None

    def upsert_document(self, path: Path, relative_path: str, chunks: list[TextChunk], *, source_type: str | None = None) -> str:
        text = path.read_text(encoding="utf-8", errors="replace")
        doc_id = "doc_" + content_hash(relative_path)[:24]
        filename = _filename_from_logical_path(relative_path, fallback=path)
        now = _now_sql()
        self.initialize()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    id, project_id, path, filename, content_hash,
                    last_indexed_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    filename=excluded.filename,
                    content_hash=excluded.content_hash,
                    last_indexed_at=excluded.last_indexed_at,
                    updated_at=excluded.updated_at
                """,
                (
                    doc_id,
                    PROJECT_ID,
                    relative_path,
                    filename,
                    content_hash(text),
                    now,
                    now,
                    now,
                ),
            )
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            vector_points: list[VectorPoint] = []
            for chunk in chunks:
                chunk_id = f"{doc_id}_chunk_{chunk.index}"
                embedding_vector = self.embedder.embed(chunk.content) if self.embedder is not None else None
                point_id = chunk_point_id(chunk_id) if embedding_vector is not None else None
                conn.execute(
                    """
                    INSERT INTO chunks (
                        id, document_id, chunk_index, heading_path, content,
                        content_hash, token_count, qdrant_point_id, embedding_model,
                        embedding_vector_json, source_type, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        doc_id,
                        chunk.index,
                        chunk.heading_path,
                        chunk.content,
                        chunk.content_hash,
                        chunk.token_count,
                        point_id,
                        self.embedder.model_name if self.embedder is not None else None,
                        json.dumps(embedding_vector) if embedding_vector is not None else None,
                        source_type,
                        now,
                        now,
                    ),
                )
                if point_id is not None and embedding_vector is not None:
                    vector_points.append(
                        VectorPoint(
                            point_id=point_id,
                            vector=embedding_vector,
                            payload={"chunk_id": chunk_id, "document_id": doc_id},
                        )
                    )
            if vector_points and self.vector_store is not None:
                self.vector_store.upsert(vector_points)
            conn.commit()
        return doc_id

    def search(self, query: str, *, top_k: int = 5, include_inactive: bool = False) -> list[SearchHit]:
        tokens = tokenize_set(query)
        if not tokens:
            return []
        self.initialize()
        with connect(self.db_path) as conn:
            hits = self._search_semantic_chunks(conn, query, top_k)
            if not hits:
                hits = _search_memories(conn, tokens, include_inactive)
                hits.extend(_search_chunks(conn, tokens))
            hits.sort(key=lambda item: item.score, reverse=True)
            results = hits[:top_k]
            self.last_trace_id = self._record_retrieval_log(conn, query, top_k, results)
            conn.commit()
            return results

    def stats(self) -> dict[str, int]:
        self.initialize()
        with connect(self.db_path) as conn:
            return {
                "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                "memory_nodes": conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0],
                "retrieval_logs": conn.execute("SELECT COUNT(*) FROM retrieval_logs").fetchone()[0],
            }

    def list_memory_nodes(self, *, status: str | None = None) -> list[MemoryNode]:
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}, expected one of {sorted(VALID_STATUSES)}")
        self.initialize()
        with connect(self.db_path) as conn:
            if status is not None:
                rows = conn.execute(
                    "SELECT * FROM memory_nodes WHERE status = ? ORDER BY created_at, id",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM memory_nodes ORDER BY created_at, id").fetchall()
            return [_memory_node_from_row(row) for row in rows]

    def set_memory_status(self, memory_id: str, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(
                f"invalid status {status!r}, expected one of {sorted(VALID_STATUSES)}"
            )
        self.initialize()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE memory_nodes SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now_sql(), memory_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"memory node not found: {memory_id!r}")
            conn.commit()

    def export_data(self) -> dict[str, object]:
        self.initialize()
        with connect(self.db_path) as conn:
            return {
                "memory_export_version": EXPORT_VERSION,
                "project_id": PROJECT_ID,
                "documents": _rows(conn, "documents"),
                "chunks": _rows(conn, "chunks"),
                "memory_nodes": _rows(conn, "memory_nodes"),
                "edges": _rows(conn, "edges"),
                "retrieval_logs": _rows(conn, "retrieval_logs"),
                "schema_migrations": _rows(conn, "schema_migrations"),
            }

    def import_data(self, payload: dict[str, object]) -> None:
        if str(payload.get("memory_export_version")) != EXPORT_VERSION:
            raise ValueError("unsupported memory export version")
        self.initialize()
        with connect(self.db_path) as conn:
            for table in EXPORT_TABLES:
                rows = payload.get(table, [])
                if not isinstance(rows, list):
                    raise ValueError(f"invalid export table: {table}")
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError(f"invalid row in table: {table}")
                    _upsert_row(conn, table, row)
            conn.commit()

    def list_retrieval_logs(self, *, limit: int = 20) -> list[RetrievalLog]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        self.initialize()
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM retrieval_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_retrieval_log_from_row(row) for row in rows]

    def get_retrieval_log(self, trace_id: str) -> RetrievalLog | None:
        self.initialize()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM retrieval_logs WHERE id = ?", (trace_id,)
            ).fetchone()
            if row is None:
                return None
            return _retrieval_log_from_row(row)

    def _record_retrieval_log(
        self,
        conn: sqlite3.Connection,
        query: str,
        top_k: int,
        results: list[SearchHit],
    ) -> str:
        trace_id = _new_id("ret")
        conn.execute(
            """
            INSERT INTO retrieval_logs (
                id, project_id, query, top_k, result_count, results_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                PROJECT_ID,
                query,
                top_k,
                len(results),
                json.dumps([hit.__dict__ for hit in results], sort_keys=True),
                _now_sql(),
            ),
        )
        return trace_id

    def _semantic_enabled(self) -> bool:
        return self.embedder is not None and self.vector_store is not None

    def _search_semantic_chunks(
        self,
        conn: sqlite3.Connection,
        query: str,
        top_k: int,
    ) -> list[SearchHit]:
        if self.embedder is None:
            return []
        assert self.embedder is not None
        query_vector = self.embedder.embed(query)
        matches = self._vector_store_matches(query_vector, top_k)
        if not matches:
            matches = _sqlite_vector_matches(conn, query_vector, top_k)
        if not matches:
            return []

        hits: list[SearchHit] = []
        for match in matches:
            row = conn.execute(
                """
                SELECT c.*, d.path
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                LEFT JOIN source_ledger sl ON sl.source_path_or_alias = d.path
                WHERE c.qdrant_point_id = ?
                  AND (sl.source_id IS NULL OR sl.status NOT IN ('missing', 'skipped'))
                """,
                (match.point_id,),
            ).fetchone()
            if row is None:
                continue
            hits.append(
                SearchHit(
                    title=row["heading_path"] or Path(row["path"]).name,
                    content=row["content"],
                    source_path=row["path"],
                    heading_path=row["heading_path"],
                    memory_type="document_chunk",
                    status="active",
                    score=match.score,
                )
            )
        return hits

    def _vector_store_matches(self, query_vector: list[float], top_k: int) -> list[VectorMatch]:
        if self.vector_store is None:
            return []
        try:
            matches = self.vector_store.search(query_vector, top_k=top_k)
        except Exception:
            return []
        return [_coerce_vector_match(match) for match in matches]


def _search_memories(
    conn: sqlite3.Connection, tokens: set[str], include_inactive: bool
) -> list[SearchHit]:
    if include_inactive:
        rows = conn.execute("SELECT * FROM memory_nodes").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM memory_nodes WHERE status IN (?, ?)", ACTIVE_STATUSES
        ).fetchall()
    hits = []
    for row in rows:
        overlap = tokens & tokenize_set(f"{row['title']} {row['content']}")
        score = round(len(overlap) / len(tokens), 4) if tokens else 0.0
        if score > 0:
            hits.append(
                SearchHit(
                    title=row["title"],
                    content=row["content"],
                    source_path=row["source_path"],
                    heading_path=None,
                    memory_type=row["type"],
                    status=row["status"],
                    score=score,
                )
            )
    return hits


def _search_chunks(conn: sqlite3.Connection, tokens: set[str]) -> list[SearchHit]:
    from truenex_memory.retrieval.scoring import BM25, tokenize, source_boost
    rows = conn.execute(
        """
        SELECT c.*, d.path
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        LEFT JOIN source_ledger sl ON sl.source_path_or_alias = d.path
        WHERE sl.source_id IS NULL OR sl.status NOT IN ('missing', 'skipped')
        """
    ).fetchall()
    if not rows:
        return []

    contents = [str(row["content"] or "") for row in rows]
    query_tokens = list(tokens)
    tokenized = [tokenize(c) for c in contents]
    bm25 = BM25(tokenized)
    scores = bm25.get_scores(query_tokens)

    hits = []
    for row, raw_score in zip(rows, scores):
        if raw_score <= 0:
            continue
        st = row["source_type"] if "source_type" in row.keys() else None
        final_score = round(raw_score * source_boost(st), 6)
        hits.append(
            SearchHit(
                title=str(row["heading_path"] or Path(str(row["path"])).name),
                content=str(row["content"] or ""),
                source_path=str(row["path"]) if row["path"] is not None else None,
                heading_path=str(row["heading_path"]) if row["heading_path"] is not None else None,
                memory_type="document_chunk",
                status="active",
                score=final_score,
            )
        )
    return hits


def _sqlite_vector_matches(
    conn: sqlite3.Connection, query_vector: list[float], top_k: int
) -> list[VectorMatch]:
    rows = conn.execute(
        """
        SELECT c.qdrant_point_id, c.embedding_vector_json
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        LEFT JOIN source_ledger sl ON sl.source_path_or_alias = d.path
        WHERE c.qdrant_point_id IS NOT NULL
          AND c.embedding_vector_json IS NOT NULL
          AND (sl.source_id IS NULL OR sl.status NOT IN ('missing', 'skipped'))
        """
    ).fetchall()
    matches: list[VectorMatch] = []
    for row in rows:
        try:
            vector = json.loads(row["embedding_vector_json"])
        except json.JSONDecodeError:
            continue
        if not isinstance(vector, list):
            continue
        score = _cosine(query_vector, [float(value) for value in vector])
        if score > 0:
            matches.append(VectorMatch(point_id=row["qdrant_point_id"], score=round(score, 4)))
    matches.sort(key=lambda item: item.score, reverse=True)
    return matches[:top_k]


def _coerce_vector_match(match: object) -> VectorMatch:
    if isinstance(match, VectorMatch):
        return match
    if isinstance(match, VectorSearchHit):
        return VectorMatch(point_id=match.id, score=match.score)
    point_id = getattr(match, "point_id", None) or getattr(match, "id", None)
    score = getattr(match, "score", 0.0)
    return VectorMatch(point_id=str(point_id), score=float(score))


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    return [dict(row) for row in rows]


def _upsert_row(conn: sqlite3.Connection, table: str, row: dict[str, object]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({column_sql}) VALUES ({placeholders})",
        [row[column] for column in columns],
    )


def _title_from_content(content: str) -> str:
    first_line = content.splitlines()[0].strip()
    return first_line[:80] or "Untitled memory"


def _filename_from_logical_path(relative_path: str, *, fallback: Path) -> str:
    cleaned = relative_path.strip().replace("\\", "/").rstrip("/")
    if not cleaned:
        return fallback.name
    name = cleaned.rsplit("/", 1)[-1]
    return name or fallback.name


def _memory_node_from_row(row: sqlite3.Row) -> MemoryNode:
    return MemoryNode(
        id=row["id"],
        project_id=row["project_id"],
        type=row["type"],
        title=row["title"],
        content=row["content"],
        status=row["status"],
        source_kind=row["source_kind"],
        source_document_id=row["source_document_id"],
        source_chunk_id=row["source_chunk_id"],
        source_path=row["source_path"],
        content_hash=row["content_hash"],
        created_by=row["created_by"],
        model_name=row["model_name"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _retrieval_log_from_row(row: sqlite3.Row) -> RetrievalLog:
    return RetrievalLog(
        id=row["id"],
        project_id=row["project_id"],
        query=row["query"],
        top_k=row["top_k"],
        result_count=row["result_count"],
        results_json=row["results_json"],
        created_at=row["created_at"],
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now_sql() -> str:
    return datetime.now(timezone.utc).isoformat()

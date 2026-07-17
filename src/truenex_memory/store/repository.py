"""Repository for local documents, chunks, memories and retrieval logs."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import logging
import os
import re
import sqlite3
import uuid

logger = logging.getLogger(__name__)

from truenex_memory.core.chunker import TextChunk, content_hash
from truenex_memory.retrieval.semantic import Embedder, VectorMatch, VectorPoint, VectorStore, chunk_point_id
from truenex_memory.store.qdrant_store import VectorSearchHit
from truenex_memory.store.models import MemoryNode, RetrievalLog, SearchHit, VALID_STATUSES
from truenex_memory.retrieval.scoring import tokenize_set
from truenex_memory.store.source_ledger import upsert_ledger_entry
from truenex_memory.store.sqlite import chunks_fts_available, connect, initialize_schema


ACTIVE_STATUSES = ("active", "unverified")
EXPORT_VERSION = "1"
PROJECT_ID = os.environ.get("TRUENEX_PROJECT_ID", "default")
METADATA_MARKER = "TRUENEX_INGESTION_METADATA"
EXPORT_TABLES = ("documents", "chunks", "memory_nodes", "edges", "retrieval_logs", "schema_migrations")


class MemoryRepository:
    """SQLite-backed local repository."""

    def __init__(
        self,
        db_path: Path,
        *,
        embedder: Embedder | None = None,
        vector_store: VectorStore | None = None,
        project_id: str | None = None,
    ) -> None:
        self.db_path = db_path
        self.embedder = embedder
        self.vector_store = vector_store
        self.project_id = project_id or os.environ.get("TRUENEX_PROJECT_ID", PROJECT_ID)
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
                    self.project_id,
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
                (self.project_id, hash_value),
            ).fetchone()
            return _memory_node_from_row(row) if row is not None else None

    def get_memory_node(self, memory_id: str) -> MemoryNode | None:
        self.initialize()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM memory_nodes WHERE id = ?", (memory_id,)
            ).fetchone()
            return _memory_node_from_row(row) if row is not None else None

    def upsert_document(
        self,
        path: Path,
        relative_path: str,
        chunks: list[TextChunk],
        *,
        source_type: str | None = None,
        update_ledger: bool = True,
    ) -> str:
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
                    self.project_id,
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
            if update_ledger:
                upsert_ledger_entry(
                    conn,
                    source_id=doc_id,
                    source_path_or_alias=relative_path,
                    source_type=source_type or "document",
                    project_name=self.project_id,
                    content_hash=content_hash(text),
                    last_indexed_at=now,
                    status="active",
                    chunk_count=len(chunks),
                )
            conn.commit()
        return doc_id

    def search(self, query: str, *, top_k: int = 5, include_inactive: bool = False) -> list[SearchHit]:
        tokens = tokenize_set(query)
        if not tokens:
            return []
        self.initialize()
        with connect(self.db_path) as conn:
            # Prefer lexical evidence whenever the query occurs in indexed
            # content.  A partially embedded database must not hide relevant
            # unembedded chunks merely because dense search returned a match.
            hits = _search_memories(conn, tokens, include_inactive, self.project_id)
            hits.extend(
                _search_chunks(
                    conn,
                    tokens,
                    self.project_id,
                    limit=max(top_k * 20, 100),
                )
            )
            hits = [hit for hit in hits if _is_searchable_source_path(hit.source_path)]
            if not hits:
                hits = self._search_semantic_chunks(conn, query, top_k)
                hits = [hit for hit in hits if _is_searchable_source_path(hit.source_path)]
            hits.sort(key=lambda item: item.score, reverse=True)
            results = _deduplicate_search_hits(hits)[:top_k]
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
                "total_tokens": conn.execute("SELECT COALESCE(SUM(token_count), 0) FROM chunks").fetchone()[0],
            }

    def list_documents(self) -> list[dict]:
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, path, filename, content_hash, last_indexed_at, created_at, updated_at FROM documents ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

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
                "project_id": self.project_id,
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
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute("PRAGMA journal_mode = MEMORY")
            for table in EXPORT_TABLES:
                rows = payload.get(table, [])
                if not isinstance(rows, list):
                    raise ValueError(f"invalid export table: {table}")
                if not rows:
                    continue
                # Normalize columns using the first row as reference
                columns = list(rows[0].keys())
                placeholders = ", ".join("?" for _ in columns)
                column_sql = ", ".join(columns)
                sql = f"INSERT OR REPLACE INTO {table} ({column_sql}) VALUES ({placeholders})"
                values = []
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError(f"invalid row in table: {table}")
                    values.append([row.get(c) for c in columns])
                conn.executemany(sql, values)
            conn.commit()
            self._repair_missing_embeddings(conn)
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("PRAGMA journal_mode = DELETE")

    def _repair_missing_embeddings(self, conn: sqlite3.Connection) -> None:
        """Recompute embeddings and qdrant_point_id for chunks that lack them."""
        if self.embedder is None:
            return
        # Fix missing qdrant_point_id
        cur = conn.execute("SELECT id FROM chunks WHERE qdrant_point_id IS NULL")
        for row in cur.fetchall():
            conn.execute(
                "UPDATE chunks SET qdrant_point_id = ? WHERE id = ?",
                (str(uuid.uuid4()), row[0]),
            )
        conn.commit()
        # Fix missing embeddings
        batch_size = 500
        while True:
            cur = conn.execute(
                "SELECT id, content FROM chunks WHERE embedding_vector_json IS NULL LIMIT ?",
                (batch_size,),
            )
            batch = cur.fetchall()
            if not batch:
                break
            texts = [r[1] for r in batch]
            embeddings = self.embedder.embed_documents(texts)
            for (chunk_id, _), emb in zip(batch, embeddings):
                conn.execute(
                    "UPDATE chunks SET embedding_vector_json = ? WHERE id = ?",
                    (json.dumps(emb), chunk_id),
                )
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
                self.project_id,
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
            matches = _sqlite_vector_matches(
                conn,
                query_vector,
                top_k,
                embedding_model=self.embedder.model_name,
            )
        if not matches:
            return []

        hits: list[SearchHit] = []
        for match in matches:
            row = conn.execute(
                """
                WITH ledger_ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY source_path_or_alias
                        ORDER BY updated_at DESC, rowid DESC
                    ) AS recency_rank
                    FROM source_ledger
                ), ledger_by_path AS (
                    SELECT source_path_or_alias, source_id, project_name,
                           CASE WHEN status NOT IN ('missing', 'skipped') THEN 1 ELSE 0 END AS allowed
                    FROM ledger_ranked
                    WHERE recency_rank = 1
                )
                SELECT c.*, d.path, sl.source_id, sl.project_name
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                LEFT JOIN ledger_by_path sl ON sl.source_path_or_alias = d.path
                WHERE c.qdrant_point_id = ?
                  AND COALESCE(sl.allowed, 1) = 1
                """,
                (match.point_id,),
            ).fetchone()
            if row is None:
                continue
            searchable_content = _strip_metadata_preamble(str(row["content"] or ""))
            if not searchable_content.strip():
                continue
            hits.append(
                SearchHit(
                    title=row["heading_path"] or Path(row["path"]).name,
                    content=searchable_content,
                    source_path=row["path"],
                    heading_path=row["heading_path"],
                    memory_type="document_chunk",
                    status="active",
                    score=match.score,
                    document_id=row["document_id"],
                    source_id=row["source_id"],
                    project=row["project_name"] or self.project_id,
                    created_at=row["created_at"],
                )
            )
        return hits

    def _vector_store_matches(self, query_vector: list[float], top_k: int) -> list[VectorMatch]:
        if self.vector_store is None:
            return []
        try:
            matches = self.vector_store.search(query_vector, top_k=top_k)
        except Exception as exc:
            logger.warning("Vector store search failed, falling back to SQLite: %s", exc)
            return []
        return [_coerce_vector_match(match) for match in matches]

    def list_sources_with_documents(self) -> list[dict]:
        if not self.db_path.exists():
            return []
        with connect(self.db_path) as conn:
            # Preload documents into memory for fast Python-side filtering
            doc_rows = conn.execute(
                "SELECT id, filename, path, content_hash, last_indexed_at FROM documents"
            ).fetchall()
            all_docs = [
                {"id": dr[0], "filename": dr[1], "path": dr[2], "content_hash": dr[3], "last_indexed_at": dr[4]}
                for dr in doc_rows
            ]
            docs_by_path: dict[str, dict] = {d["path"].replace("\\", "/"): d for d in all_docs}

            rows = conn.execute(
                """
                SELECT source_id, project_name, source_type, source_path_or_alias,
                       status, last_indexed_at, chunk_count
                FROM source_ledger
                WHERE status = 'active'
                """
            ).fetchall()

            result: list[dict] = []
            for row in rows:
                source_id = row[0]
                project_name = row[1]
                source_type = row[2]
                source_path = row[3]
                status = row[4]
                last_indexed = row[5]
                chunk_count = row[6]

                # Derive project_name from path when null
                derived_project = project_name
                if not derived_project:
                    parts = source_path.replace("\\", "/").split("/")
                    for i, part in enumerate(parts):
                        lower = part.lower()
                        if lower in ("sofware", "software", "projectpy", "projects", "src", "documents", "documenti", "workspace", "dev"):
                            if i + 1 < len(parts):
                                derived_project = parts[i + 1]
                                break
                    if not derived_project and len(parts) >= 2:
                        derived_project = parts[-2]
                source_name = derived_project or Path(source_path).stem or "Unknown"

                if source_type == "server_alias":
                    doc_count = 0
                    docs: list[dict] = []
                elif source_type == "agent_session":
                    normalized = source_path.replace("\\", "/")
                    doc = docs_by_path.get(normalized)
                    docs = [doc] if doc else []
                    doc_count = len(docs)
                else:
                    normalized = source_path.replace("\\", "/")
                    # Try exact match first (file-level source)
                    doc = docs_by_path.get(normalized)
                    if doc:
                        docs = [doc]
                        doc_count = 1
                    else:
                        # Fall back to directory prefix match
                        prefix = normalized.rstrip("/") + "/"
                        matched = [d for d in all_docs if d["path"].replace("\\", "/").startswith(prefix)]
                        doc_count = len(matched)
                        docs = matched[:20]

                result.append({
                    "source_id": source_id,
                    "source_name": source_name,
                    "source_type": source_type,
                    "source_path_or_alias": source_path,
                    "status": status,
                    "last_indexed_at": last_indexed,
                    "chunk_count": chunk_count,
                    "document_count": doc_count,
                    "documents": docs,
                })

            result.sort(key=lambda x: x["document_count"], reverse=True)
            return result


    def get_file_metadata(self, document_id: str) -> dict:
        """Extract structural metadata from a file on disk (AST, headings, keys)."""
        if not self.db_path.exists():
            return {"error": "db not found"}

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT path, filename FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        if not row:
            return {"error": "document not found"}

        file_path = Path(row["path"])
        if not file_path.exists():
            return {"error": "file not found on disk"}

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            return {"error": f"read failed: {exc}"}

        ext = Path(row["filename"]).suffix.lower()
        result: dict[str, Any] = {"file_type": ext, "file_path": str(file_path)}

        if ext == ".py":
            result.update(self._parse_python(content))
        elif ext in (".md", ".markdown", ".mdx"):
            result.update(self._parse_markdown(content))
        elif ext == ".json":
            result.update(self._parse_json(content))
        elif ext in (".yaml", ".yml"):
            result.update(self._parse_yaml(content))
        elif ext == ".toml":
            result.update(self._parse_toml(content))
        else:
            result["unsupported"] = True

        return result

    def analyze_file_content(self, file_id: str) -> dict:
        """Extract symbols from chunk content via regex (used by frontend file-analysis)."""
        if not self.db_path.exists():
            return {"error": "db not found"}
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT filename FROM documents WHERE id = ?", (file_id,)
            ).fetchone()
            if not row:
                return {"error": "document not found"}
            chunks = conn.execute(
                "SELECT content FROM chunks WHERE document_id = ? ORDER BY chunk_index", (file_id,)
            ).fetchall()
        if not chunks:
            return {"error": "no chunks found for document"}
        content = "\n".join(c["content"] for c in chunks)
        ext = Path(row["filename"]).suffix.lower()
        result: dict[str, Any] = {"file_type": ext}
        if ext == ".py":
            result["functions"] = re.findall(r"^def\s+(\w+)", content, re.MULTILINE)[:50]
            result["classes"] = re.findall(r"^class\s+(\w+)", content, re.MULTILINE)[:50]
            result["imports"] = re.findall(r"^(?:from\s+(\S+)\s+import|import\s+(\S+))", content, re.MULTILINE)[:50]
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            result["imports"] = re.findall(r"^import\s+.*?from\s+['\"](.+?)['\"]", content, re.MULTILINE)[:50]
        elif ext in (".md", ".markdown", ".mdx"):
            result["headings"] = [
                {"level": len(m.group(1)), "text": m.group(2).strip()}
                for m in re.finditer(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE)
            ][:50]
        elif ext == ".json":
            try:
                data = json.loads(content)
                result["schema_keys"] = list(data.keys())[:50] if isinstance(data, dict) else []
            except json.JSONDecodeError:
                result["schema_keys"] = []
        elif ext in (".yaml", ".yml"):
            result["schema_keys"] = re.findall(r"^(\w+):", content, re.MULTILINE)[:50]
        else:
            result["unsupported"] = True
        return result

    def get_source(self, source_id: str) -> dict | None:
        if not self.db_path.exists():
            return None
        with connect(self.db_path) as conn:
            source = conn.execute(
                "SELECT * FROM source_ledger WHERE source_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                return None

            source_path = source["source_path_or_alias"]
            source_type = source["source_type"]

            if source_type == "server_alias":
                doc_rows = []
            elif source_type == "agent_session":
                doc_rows = conn.execute(
                    "SELECT id, filename, path, content_hash, last_indexed_at FROM documents WHERE path = ?",
                    (source_path,),
                ).fetchall()
            else:
                doc_rows = conn.execute(
                    "SELECT id, filename, path, content_hash, last_indexed_at FROM documents WHERE path = ?",
                    (source_path,),
                ).fetchall()
                if not doc_rows:
                    prefix = source_path.replace("\\", "/").rstrip("/") + "/"
                    doc_rows = conn.execute(
                        "SELECT id, filename, path, content_hash, last_indexed_at FROM documents WHERE REPLACE(path, '\\\\', '/') LIKE ?",
                        (prefix + "%",),
                    ).fetchall()

            docs = [
                {
                    "id": dr["id"],
                    "filename": dr["filename"],
                    "path": dr["path"],
                    "content_hash": dr["content_hash"],
                    "last_indexed_at": dr["last_indexed_at"],
                }
                for dr in doc_rows
            ]

            doc_ids = [d["id"] for d in docs]
            chunk_count = 0
            if doc_ids:
                placeholders = ",".join("?" for _ in doc_ids)
                row = conn.execute(
                    f"SELECT COUNT(*) FROM chunks WHERE document_id IN ({placeholders})",
                    doc_ids,
                ).fetchone()
                chunk_count = row[0] if row else 0

            edge_rows = conn.execute(
                "SELECT target_node_id, relation_type FROM edges WHERE source_node_id = ?",
                (source_id,),
            ).fetchall()
            relations = [
                {"target": r["target_node_id"], "type": r["relation_type"]}
                for r in edge_rows
            ]

            project_name = source["project_name"] or _derive_project_name(source_path)

            return {
                "id": source["source_id"],
                "path": source["source_path_or_alias"],
                "project": project_name,
                "documents": docs,
                "relations": relations,
                "chunk_count": chunk_count,
                "last_indexed": source["last_indexed_at"],
            }

    def catalog_status(self) -> dict:
        if not self.db_path.exists():
            return {
                "status": "stale",
                "last_refresh": None,
                "warnings": ["Database not found"],
                "total_sources": 0,
                "total_chunks": 0,
            }
        with connect(self.db_path) as conn:
            total_sources = conn.execute("SELECT COUNT(*) FROM source_ledger").fetchone()[0]
            total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

            row = conn.execute(
                "SELECT MAX(last_indexed_at) FROM source_ledger WHERE last_indexed_at IS NOT NULL"
            ).fetchone()
            last_refresh = row[0] if row else None

            status = "healthy"
            warnings: list[str] = []

            if last_refresh:
                try:
                    last_dt = datetime.fromisoformat(last_refresh)
                    now = datetime.now(timezone.utc)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    days_since = (now - last_dt).days
                    if days_since > 7:
                        status = "stale"
                        warnings.append(f"Last index was {days_since} days ago")
                except ValueError:
                    warnings.append("Invalid last_indexed_at timestamp")
            else:
                status = "stale"
                warnings.append("No indexing timestamp found")

            return {
                "status": status,
                "last_refresh": last_refresh,
                "warnings": warnings,
                "total_sources": total_sources,
                "total_chunks": total_chunks,
            }

    def _parse_python(self, content: str) -> dict:
        import ast
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            return {"parse_error": str(exc)}
        functions = []
        classes = []
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions.append(node.name)
            elif isinstance(node, ast.AsyncFunctionDef):
                functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    imports.append(f"{mod}.{alias.name}" if mod else alias.name)
        return {
            "functions": functions[:50],
            "classes": classes[:50],
            "imports": imports[:50],
            "function_count": len(functions),
            "class_count": len(classes),
            "import_count": len(imports),
        }

    def _parse_markdown(self, content: str) -> dict:
        import re
        headings = []
        for line in content.splitlines():
            m = re.match(r'^(#{1,6})\s+(.+)$', line)
            if m:
                level = len(m.group(1))
                headings.append({"level": level, "text": m.group(2).strip()})
        return {"headings": headings[:100], "heading_count": len(headings)}

    def _parse_json(self, content: str) -> dict:
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                keys = list(data.keys())
                return {"keys": keys[:100], "key_count": len(keys), "type": "object"}
            elif isinstance(data, list):
                return {"type": "array", "length": len(data)}
            else:
                return {"type": type(data).__name__}
        except json.JSONDecodeError as exc:
            return {"parse_error": str(exc)}

    def _parse_yaml(self, content: str) -> dict:
        try:
            import yaml
            data = yaml.safe_load(content)
            if isinstance(data, dict):
                keys = list(data.keys())
                return {"keys": keys[:100], "key_count": len(keys), "type": "mapping"}
            elif isinstance(data, list):
                return {"type": "sequence", "length": len(data)}
            else:
                return {"type": type(data).__name__}
        except Exception:
            # Fallback: extract top-level keys by regex
            import re
            keys = re.findall(r'^(\w+):', content, re.MULTILINE)
            return {"keys": keys[:100], "key_count": len(keys), "type": "mapping (fallback)"}

    def _parse_toml(self, content: str) -> dict:
        try:
            import tomllib
            data = tomllib.loads(content)
            keys = list(data.keys())
            return {"keys": keys[:100], "key_count": len(keys), "type": "table"}
        except Exception:
            # Fallback: extract [table] headers and bare keys
            import re
            tables = re.findall(r'^\[(\w+)\]', content, re.MULTILINE)
            keys = re.findall(r'^(\w+)\s*=', content, re.MULTILINE)
            return {"tables": tables[:50], "keys": keys[:100], "type": "table (fallback)"}

def _search_memories(
    conn: sqlite3.Connection, tokens: set[str], include_inactive: bool, project_id: str = PROJECT_ID
) -> list[SearchHit]:
    source_lookup = {
        row["source_path_or_alias"]: (row["source_id"], row["project_name"])
        for row in conn.execute(
            "SELECT source_id, source_path_or_alias, project_name FROM source_ledger"
        ).fetchall()
    }
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
            source_id, project = source_lookup.get(row["source_path"] or "", (None, project_id))
            hits.append(
                SearchHit(
                    title=row["title"],
                    content=row["content"],
                    source_path=row["source_path"],
                    heading_path=None,
                    memory_type=row["type"],
                    status=row["status"],
                    score=score,
                    document_id=row["source_document_id"],
                    source_id=source_id,
                    project=project or project_id,
                    created_at=row["created_at"],
                )
            )
    return hits


def _search_chunks(
    conn: sqlite3.Connection,
    tokens: set[str],
    project_id: str = PROJECT_ID,
    *,
    limit: int = 100,
) -> list[SearchHit]:
    if chunks_fts_available(conn):
        try:
            return _search_chunks_fts(conn, tokens, project_id, limit=limit)
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 chunk search failed, falling back to Python BM25: %s", exc)
    return _search_chunks_bm25(conn, tokens, project_id, limit=limit)


def _search_chunks_fts(
    conn: sqlite3.Connection,
    tokens: set[str],
    project_id: str,
    *,
    limit: int,
) -> list[SearchHit]:
    from truenex_memory.retrieval.scoring import source_boost

    query = _fts_or_query(tokens)
    if not query:
        return []
    rows = conn.execute(
        """
        WITH ledger_ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY source_path_or_alias
                ORDER BY updated_at DESC, rowid DESC
            ) AS recency_rank
            FROM source_ledger
        ), ledger_by_path AS (
            SELECT source_path_or_alias, source_id, project_name,
                   CASE WHEN status NOT IN ('missing', 'skipped') THEN 1 ELSE 0 END AS allowed
            FROM ledger_ranked
            WHERE recency_rank = 1
        )
        SELECT c.*, d.path, sl.source_id, sl.project_name,
               bm25(chunks_fts, 1.0, 2.0) AS fts_rank
        FROM chunks_fts
        JOIN chunks c ON c.rowid = chunks_fts.rowid
        JOIN documents d ON d.id = c.document_id
        LEFT JOIN ledger_by_path sl ON sl.source_path_or_alias = d.path
        WHERE chunks_fts MATCH ?
          AND COALESCE(sl.allowed, 1) = 1
        ORDER BY fts_rank ASC
        LIMIT ?
        """,
        (query, limit),
    ).fetchall()

    hits: list[SearchHit] = []
    for row in rows:
        searchable_content = _strip_metadata_preamble(str(row["content"] or ""))
        if not searchable_content.strip():
            continue
        raw_score = max(0.0, -float(row["fts_rank"]))
        source_type = row["source_type"] if "source_type" in row.keys() else None
        hits.append(
            SearchHit(
                title=str(row["heading_path"] or Path(str(row["path"])).name),
                content=searchable_content,
                source_path=str(row["path"]) if row["path"] is not None else None,
                heading_path=str(row["heading_path"]) if row["heading_path"] is not None else None,
                memory_type="document_chunk",
                status="active",
                score=round(raw_score * 10.0 * source_boost(source_type), 6),
                document_id=row["document_id"],
                source_id=row["source_id"],
                project=row["project_name"] or project_id,
                created_at=row["created_at"],
            )
        )
    hits.sort(key=lambda item: item.score, reverse=True)
    return hits


def _search_chunks_bm25(
    conn: sqlite3.Connection,
    tokens: set[str],
    project_id: str,
    *,
    limit: int,
) -> list[SearchHit]:
    from truenex_memory.retrieval.scoring import BM25, tokenize, source_boost
    rows = conn.execute(
        """
        WITH ledger_ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY source_path_or_alias
                ORDER BY updated_at DESC, rowid DESC
            ) AS recency_rank
            FROM source_ledger
        ), ledger_by_path AS (
            SELECT source_path_or_alias, source_id, project_name,
                   CASE WHEN status NOT IN ('missing', 'skipped') THEN 1 ELSE 0 END AS allowed
            FROM ledger_ranked
            WHERE recency_rank = 1
        )
        SELECT c.*, d.path, sl.source_id, sl.project_name
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        LEFT JOIN ledger_by_path sl ON sl.source_path_or_alias = d.path
        WHERE COALESCE(sl.allowed, 1) = 1
        """
    ).fetchall()
    if not rows:
        return []

    contents = [_strip_metadata_preamble(str(row["content"] or "")) for row in rows]
    query_tokens = list(tokens)
    tokenized = [tokenize(c) for c in contents]
    bm25 = BM25(tokenized)
    scores = bm25.get_scores(query_tokens)

    hits = []
    for row, searchable_content, raw_score in zip(rows, contents, scores):
        if raw_score <= 0 or not searchable_content.strip():
            continue
        st = row["source_type"] if "source_type" in row.keys() else None
        final_score = round(raw_score * source_boost(st), 6)
        hits.append(
            SearchHit(
                title=str(row["heading_path"] or Path(str(row["path"])).name),
                content=searchable_content,
                source_path=str(row["path"]) if row["path"] is not None else None,
                heading_path=str(row["heading_path"]) if row["heading_path"] is not None else None,
                memory_type="document_chunk",
                status="active",
                score=final_score,
                document_id=row["document_id"],
                source_id=row["source_id"],
                project=row["project_name"],
                created_at=row["created_at"],
            )
        )
    hits.sort(key=lambda item: item.score, reverse=True)
    return hits[:limit]


def _fts_or_query(tokens: set[str]) -> str:
    """Build a literal-token OR query safe for SQLite FTS5 MATCH."""

    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in sorted(tokens))


def _strip_metadata_preamble(content: str) -> str:
    """Remove ingestion-only metadata so it cannot leak into search results."""

    text = content.lstrip()
    if not text.startswith(METADATA_MARKER):
        return content
    parts = re.split(r"\r?\n\s*\r?\n", text, maxsplit=1)
    if len(parts) == 2:
        return parts[1].lstrip()
    # The chunker may isolate the metadata paragraph in its own chunk.
    return ""


def _is_searchable_source_path(source_path: str | None) -> bool:
    """Reject operating-system trash locations that can never be valid evidence."""

    if not source_path:
        return True
    normalized = "/" + source_path.replace("\\", "/").casefold().strip("/") + "/"
    blocked_segments = (
        "/$recycle.bin/",
        "/recycler/",
        "/system volume information/",
        "/.trash/",
        "/.trashes/",
    )
    return not any(segment in normalized for segment in blocked_segments)


def _deduplicate_search_hits(hits: list[SearchHit]) -> list[SearchHit]:
    """Keep the highest-ranked copy of equivalent indexed content."""

    unique: list[SearchHit] = []
    seen: set[tuple[str, str]] = set()
    for hit in hits:
        normalized_content = " ".join(hit.content.split()).casefold()
        key = (hit.memory_type, normalized_content)
        if normalized_content and key in seen:
            continue
        if normalized_content:
            seen.add(key)
        unique.append(hit)
    return unique


def _sqlite_vector_matches(
    conn: sqlite3.Connection,
    query_vector: list[float],
    top_k: int,
    *,
    embedding_model: str,
) -> list[VectorMatch]:
    rows = conn.execute(
        """
        WITH ledger_ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY source_path_or_alias
                ORDER BY updated_at DESC, rowid DESC
            ) AS recency_rank
            FROM source_ledger
        ), ledger_by_path AS (
            SELECT source_path_or_alias,
                   CASE WHEN status NOT IN ('missing', 'skipped') THEN 1 ELSE 0 END AS allowed
            FROM ledger_ranked
            WHERE recency_rank = 1
        )
        SELECT c.qdrant_point_id, c.embedding_vector_json
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        LEFT JOIN ledger_by_path sl ON sl.source_path_or_alias = d.path
        WHERE c.qdrant_point_id IS NOT NULL
          AND c.embedding_vector_json IS NOT NULL
          AND c.embedding_model = ?
          AND COALESCE(sl.allowed, 1) = 1
        """,
        (embedding_model,),
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


def _derive_project_name(source_path: str) -> str:
    parts = source_path.replace("\\", "/").split("/")
    for i, part in enumerate(parts):
        lower = part.lower()
        if lower in ("sofware", "software", "projectpy", "projects", "src", "documents", "documenti", "workspace", "dev"):
            if i + 1 < len(parts):
                return parts[i + 1]
    if len(parts) >= 2:
        return parts[-2]
    return Path(source_path).stem or "Unknown"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now_sql() -> str:
    return datetime.now(timezone.utc).isoformat()

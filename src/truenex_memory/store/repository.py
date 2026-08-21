"""Repository for local documents, chunks, memories and retrieval logs."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import os
import re
import sqlite3
import uuid

logger = logging.getLogger(__name__)

from truenex_memory.core.chunker import TextChunk, content_hash
from truenex_memory.retrieval.fusion import (
    DENSE_FUSION_MIN_COSINE,
    DENSE_SOURCE_WEIGHT,
    MAX_CHUNKS_PER_DOCUMENT,
    MEMORY_FUSION_MIN_OVERLAP,
    MEMORY_GATE_NEAR_VERBATIM_OVERLAP,
)
from truenex_memory.retrieval.semantic import Embedder, VectorMatch, VectorPoint, VectorStore, chunk_point_id
from truenex_memory.store.qdrant_store import VectorSearchHit
from truenex_memory.store.models import MemoryNode, RetrievalLog, SearchHit, VALID_STATUSES
from truenex_memory.retrieval.expansion import expand_for_chunks
from truenex_memory.retrieval.reranker import (
    CrossEncoderReranker,
    RerankerConfig,
    reranker_config_from_env,
)
from truenex_memory.retrieval.scoring import (
    content_tokens,
    content_tokens_from,
    document_frequencies,
    most_informative_tokens,
    tokenize_set,
)
from truenex_memory.store.source_ledger import upsert_ledger_entry
from truenex_memory.store.sqlite import chunks_fts_available, connect, initialize_schema


ACTIVE_STATUSES = ("active", "unverified")
EXPORT_VERSION = "1"
PROJECT_ID = os.environ.get("TRUENEX_PROJECT_ID", "default")
METADATA_MARKER = "TRUENEX_INGESTION_METADATA"
EXPORT_TABLES = ("documents", "chunks", "memory_nodes", "edges", "retrieval_logs", "schema_migrations")

# Reciprocal Rank Fusion (RRF) constants for merging heterogeneous rankers.
# These values are duplicated in truenex_memory.retrieval.fusion (used by
# the CLI `global search` path); the two copies MUST stay aligned — a
# parity test in tests/unit/test_global_search_fusion.py enforces it.
# MEMORY_FUSION_MIN_OVERLAP (the pre-fusion memory relevance gate) and
# DENSE_SOURCE_WEIGHT (the semantic third ranker, MCP path only) are NOT
# duplicated: they are imported from truenex_memory.retrieval.fusion, the
# single source for them.
# RRF_K is the standard rank-smoothing constant (Cormack et al., 2009): it
# damps the influence of top ranks so rank 1 does not dominate rank 5.
RRF_K = 60
# Memories are curated knowledge written explicitly by an agent or a person,
# not text extracted automatically from a file. At equal position within
# their own ranking they now enter fusion at equal weight with document
# chunks: the old 1.5 buried documentation entirely (see the rationale and
# the measurements in retrieval.fusion, the aligned copy of this constant).
MEMORY_SOURCE_WEIGHT = 1.0
CHUNK_SOURCE_WEIGHT = 1.0


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
        supersedes: str | None = None,
    ) -> str:
        """Write a memory node, optionally retiring the one it replaces.

        ``supersedes`` is part of this call rather than a separate step on
        purpose: a link left as optional housekeeping does not get made,
        and a supersession mechanism nobody uses is worse than none — it
        promises that retrieval only returns current facts while stale
        alarms keep surfacing. Retiring the old note and writing the new
        one happen in a single transaction, so there is no window where
        both read as current, or where the old one is retired and the
        replacement missing.
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}, expected one of {sorted(VALID_STATUSES)}")
        now = _now_sql()
        memory_id = _new_id("mem")
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("memory content cannot be empty")
        self.initialize()
        with connect(self.db_path) as conn:
            if supersedes is not None:
                existing = conn.execute(
                    "SELECT id, status FROM memory_nodes WHERE id = ?", (supersedes,)
                ).fetchone()
                if existing is None:
                    raise ValueError(f"cannot supersede unknown memory {supersedes!r}")
                if existing["status"] == "superseded":
                    raise ValueError(
                        f"memory {supersedes!r} is already superseded; "
                        "supersede the note that replaced it instead"
                    )
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
            if supersedes is not None:
                conn.execute(
                    """
                    UPDATE memory_nodes
                    SET status = 'superseded', superseded_by = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (memory_id, now, supersedes),
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
                # Use embed_documents (asymmetric "passage: " prefix for e5)
                # when the embedder exposes it — same getattr pattern as
                # embed_query in _search_semantic_chunks — so chunks indexed
                # after activation get correctly prefixed vectors.
                embedding_vector: list[float] | None = None
                if self.embedder is not None:
                    embed_documents = getattr(self.embedder, "embed_documents", None)
                    if callable(embed_documents):
                        embedding_vector = embed_documents([chunk.content])[0]
                    else:
                        embedding_vector = self.embedder.embed(chunk.content)
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

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        include_inactive: bool = False,
        include_sessions: bool = False,
        scope: str | None = None,
        max_per_document: int | None = MAX_CHUNKS_PER_DOCUMENT,
        diagnostics: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Rank memories and document chunks for *query*.

        ``include_sessions`` re-admits memories derived from agent
        conversation transcripts, which are excluded by default: they are
        raw dialogue, they match query tokens as easily as curated content,
        and they crowd documentation out of the top results. Pass it when
        the conversation history itself is what you are looking for.
        """
        tokens = tokenize_set(query)
        if not tokens:
            return []
        self.initialize()
        with connect(self.db_path) as conn:
            # Prefer lexical evidence whenever the query occurs in indexed
            # content.  A partially embedded database must not hide relevant
            # unembedded chunks merely because dense search returned a match.
            memory_hits = _search_memories(
                conn,
                tokens,
                include_inactive,
                self.project_id,
                include_sessions=include_sessions,
            )
            # Expand Italian domain terms to their English equivalents for
            # the chunk branch ONLY. The memory branch keeps the unexpanded
            # `tokens`: its score is an overlap ratio, so a wider query
            # would move the denominator and silently shift the memory
            # relevance gate. Because memory ranks are untouched, this
            # cannot regress memory recall.
            chunk_hits = _search_chunks(
                conn,
                expand_for_chunks(tokens),
                self.project_id,
                limit=max(top_k * 20, 100),
                scope=scope,
            )
            scope_fell_back = False
            if scope and not chunk_hits:
                # Ripiego globale quando lo scope non produce nulla.
                #
                # E' la differenza fra una restrizione utile e una trappola:
                # misurato il 2026-08-21, con uno scope sbagliato la ricerca
                # ristretta passa da 2/32 a 0/32 su entrambi gli insiemi di
                # valutazione, cioe' non peggiora, AZZERA. Senza questo
                # ripiego lo scope non potrebbe essere acceso per default:
                # un errore del chiamante, o una domanda legittimamente
                # cross-progetto, renderebbero la risposta irraggiungibile
                # invece che soltanto peggiore.
                #
                # Il ripiego scatta solo sul vuoto, non sui risultati deboli:
                # decidere che una risposta ristretta e' "troppo debole" e
                # rifare la ricerca richiederebbe una soglia di qualita' che
                # non ho modo di tarare senza altri dati.
                chunk_hits = _search_chunks(
                    conn,
                    expand_for_chunks(tokens),
                    self.project_id,
                    limit=max(top_k * 20, 100),
                )
                scope_fell_back = bool(chunk_hits)
                if scope_fell_back:
                    logger.info(
                        "scope %r non ha prodotto candidati: ricerca ripetuta "
                        "sull'intero corpus", scope,
                    )
            if not include_sessions:
                # Session transcripts are indexed twice: as memory nodes
                # (filtered above) and as chunks of the .jsonl file itself.
                # Excluding only the first half would leave the same raw
                # dialogue competing with documentation through the other.
                # Chunks of a .jsonl session file are raw dialogue with no
                # vetting step of their own — only memory NODES can be
                # approved — so the path test is the whole story here.
                chunk_hits = [
                    hit for hit in chunk_hits if not _is_session_derived(hit.source_path)
                ]
            # Filter each source before fusion: memory scores (0.0-1.0) and
            # chunk BM25 scores (hundreds) are not comparable, so they must be
            # merged by rank (RRF), never by raw score.
            memory_hits = [hit for hit in memory_hits if _is_searchable_source_path(hit.source_path)]
            chunk_hits = [hit for hit in chunk_hits if _is_searchable_source_path(hit.source_path)]
            # Relevance gate on memories BEFORE fusion: RRF ignores raw
            # scores, so any memory matching a single query token would
            # enter the fused ranking with the 1.5 source weight and a long
            # tail of weak memories would push every document chunk out of
            # top_k. The memory raw score IS interpretable (fraction of
            # query tokens covered), so memories below the threshold are
            # noise, not evidence.
            # The gate applies ONLY when chunk evidence exists: it exists to
            # free strong documents from the weak-memory tail, but when a
            # weak memory is the ONLY lexical evidence it must be kept —
            # the dense fallback with HashingEmbedder would be noise on a
            # non-RRF score scale. Consequently the semantic fallback below
            # sees the POST-gate lists and triggers only on genuinely zero
            # lexical hits.
            if chunk_hits:
                memory_hits = [
                    hit for hit in memory_hits if hit.score >= MEMORY_FUSION_MIN_OVERLAP
                ]
                memory_hits = _require_most_informative_token(
                    conn, memory_hits, content_tokens_from(tokens)
                )
            # Dense (semantic) candidates as a third RRF ranker, computed
            # ALWAYS when the active embedder is a real semantic backend
            # (not the hashing fallback) — the old "only when lexical is
            # empty" fallback made dense search unreachable in practice.
            # The SQL in _sqlite_vector_matches filters by
            # chunks.embedding_model, so with no vectors for the active
            # model (e.g. before reindex) this returns [] cheaply and the
            # fusion stays lexical-only.
            dense_hits: list[SearchHit] = []
            rerank = reranker_config_from_env()
            # The reranker needs dense candidates even when the dense branch
            # does not enter the fusion: on questions phrased without the
            # document's own words the dense list is the only place the target
            # appears (measured: union of 200 lexical + 200 dense contains it
            # in 15/30 such cases, the lexical list alone in 9/30).
            feeding_reranker = rerank.enabled and self.embedder is not None
            if self._dense_ranker_enabled() or feeding_reranker:
                # No absolute cosine floor when the candidates only feed the
                # reranker. The 0.90 gate exists to keep weak neighbours out
                # of the RRF ranking, where a raw score is trusted as
                # evidence; a cross-encoder re-reads every candidate against
                # the query, so its input wants the TOP of the cosine
                # ranking, whatever its absolute value. On the paraphrase set
                # that gate admits exactly zero candidates, which is why the
                # first integrated attempt scored 4/30 instead of the 6/30
                # the standalone experiment reached on the development set:
                # the reranker was being handed an empty dense pool.
                dense_hits = self._search_semantic_chunks(
                    conn,
                    query,
                    max(top_k * 20, 100),
                    min_score=None if feeding_reranker else DENSE_FUSION_MIN_COSINE,
                )
                dense_hits = [
                    hit for hit in dense_hits if _is_searchable_source_path(hit.source_path)
                ]
                if not include_sessions:
                    dense_hits = [
                        hit for hit in dense_hits
                        if not _is_session_derived(hit.source_path)
                    ]
                # Relevance gate on dense candidates BEFORE fusion,
                # symmetric to the memory gate above: on ~478k embedded
                # chunks every query has "plausible but irrelevant" dense
                # neighbours (cosine 0.84-0.93 measured) and RRF would let
                # them bury the lexical/memory targets (memory-recall
                # hit@k 0.93 -> 0.64 ungated, eval 2026-07-27). The e5
                # cosine is interpretable; only near-exact semantic
                # matches (>= DENSE_FUSION_MIN_COSINE) corroborate.
                # Intentionally REDUNDANT with the pre-hydration
                # min_score filter in _search_semantic_chunks (same
                # threshold, same scores): defense in depth at zero cost,
                # in case a future caller skips the pre-filter.
                if not feeding_reranker:
                    dense_hits = [
                        hit for hit in dense_hits if hit.score >= DENSE_FUSION_MIN_COSINE
                    ]
            if not memory_hits and not chunk_hits and not dense_hits:
                # Legacy fallback (extrema ratio): no lexical hits AND no
                # dense candidates — e.g. hashing embedder with its own
                # vectors, or a semantic model with no vectors yet. Kept for
                # backwards compatibility with pre-RRF dense behavior.
                # NOTE: the DENSE_FUSION_MIN_COSINE gate intentionally does
                # NOT apply on this path: it exists to preserve the pre-RRF
                # behavior with the hashing embedder, whose scores are not
                # cosines (a 0.90 threshold would always zero it out).
                hits = self._search_semantic_chunks(conn, query, top_k)
                hits = [hit for hit in hits if _is_searchable_source_path(hit.source_path)]
                hits.sort(key=lambda item: item.score, reverse=True)
            else:
                # Ungated dense candidates must never reach the fusion: they
                # are reranker input, not first-stage evidence.
                fused_dense = (
                    [h for h in dense_hits if h.score >= DENSE_FUSION_MIN_COSINE]
                    if self._dense_ranker_enabled()
                    else []
                )
                hits = _fuse_ranked_hits(memory_hits, chunk_hits, fused_dense)
            hits = _deduplicate_search_hits(hits, max_per_document=max_per_document)
            if rerank.enabled:
                hits = _rerank_hits(query, hits, dense_hits, rerank, conn)
            results = hits[:top_k]
            if diagnostics is not None:
                # Da quale progetto arriva la risposta, e se lo scope e' stato
                # davvero applicato.
                #
                # Due comportamenti silenziosi vivevano qui. Il ripiego globale
                # sullo scope che non trova nulla era registrato solo nel log:
                # chi chiama riceveva risultati di tutto il corpus credendoli
                # ristretti. E uno scope esistente-ma-sbagliato (`tauri-app`
                # invece di `truenex-memory`) restituisce documenti coerenti e
                # del progetto sbagliato, senza niente che lo segnali.
                #
                # Nessuno dei due si risolve chiedendo a chi cerca di stare
                # attento: la provenienza va scritta nella risposta, cosi'
                # l'incongruenza fra cio' che si e' chiesto e cio' che si e'
                # ottenuto e' visibile a chi legge, umano o agente.
                diagnostics.update(
                    scope=scope,
                    scope_applied=bool(scope) and not scope_fell_back,
                    scope_fell_back=scope_fell_back,
                    answered_from=sorted(
                        {hit.project for hit in results if hit.project}
                    ),
                )
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

    def _dense_ranker_enabled(self) -> bool:
        """True when the dense ranker is switched on AND semantically usable.

        OFF BY DEFAULT since 2026-08-21. Set TRUENEX_DENSE=on to re-enable;
        nothing else is needed, the vector cache is left in place, so this
        is reversible without re-embedding the corpus.

        Why off. Measured on the live store (201,282 chunks, e5-base,
        53-case eval, paired per case):

        - varying the branch WEIGHT at the unchanged 0.90 cosine gate:
          0.9 -> 40/53, 0.5 -> 40/53, 0.0 -> 42/53. The branch helps no
          category and, switched off, improves two (memory-recall 12->13,
          real-logs 2->3);
        - for the nine failing documentation queries the candidates above
          the gate were ZERO, with max cosine 0.845-0.883;
        - lowering the gate (0.86 / 0.80 / 0.70 / 0.0) gives 33/53 at every
          value: it is the TOP candidates by cosine that are wrong, so this
          is not a threshold that needs tuning.

        Together those say the model's ordering on this corpus is not
        aligned — the right chunk is not at the top of the cosine — and no
        weight or threshold repairs a ranker that orders badly.

        What is NOT established: the eval contains no paraphrase, synonym
        or cross-lingual case, i.e. exactly the class where a dense branch
        should earn its place. Switching it off optimises for the metric we
        have, not necessarily for the product. To justify turning it back
        on, measure dense-only recall@k on such queries first; if the
        correct chunk never appears in the cosine top-50, the answer is a
        different embedding model, not this flag.

        The hashing fallback embedder is excluded regardless: its vectors
        carry no semantic content, so as an always-on RRF ranker it would
        inject pure noise. Detection prefers the embedder's metadata
        backend and falls back to the persisted model_name prefix.
        """

        if os.environ.get("TRUENEX_DENSE", "off").strip().lower() != "on":
            return False
        if self.embedder is None:
            return False
        metadata = getattr(self.embedder, "metadata", None)
        backend = getattr(metadata, "backend", None)
        if backend is not None:
            return backend != "hashing"
        return not str(getattr(self.embedder, "model_name", "")).startswith("hashing-fallback:")

    def _search_semantic_chunks(
        self,
        conn: sqlite3.Connection,
        query: str,
        top_k: int,
        *,
        min_score: float | None = None,
    ) -> list[SearchHit]:
        if self.embedder is None:
            return []
        assert self.embedder is not None
        # Use the asymmetric query prefix when the embedder provides it
        # (e5 family requires "query: " / "passage: " prefixes).
        embed_query = getattr(self.embedder, "embed_query", None)
        if callable(embed_query):
            query_vector = embed_query(query)
        else:
            query_vector = self.embedder.embed(query)
        matches = self._vector_store_matches(query_vector, top_k)
        used_vector_index = False
        if not matches:
            # Fast path: in-process numpy matrix (BLAS matvec) cached per
            # (db_path, embedding_model). Returns None when numpy is missing
            # (legacy Python scan below) or the model has no vectors.
            from truenex_memory.retrieval.vector_index import get_index, search_index

            index = get_index(self.db_path, conn, self.embedder.model_name)
            if index is not None:
                matches = search_index(index, query_vector, top_k)
                used_vector_index = True
        if not matches and not used_vector_index:
            matches = _sqlite_vector_matches(
                conn,
                query_vector,
                top_k,
                embedding_model=self.embedder.model_name,
            )
        if min_score is not None:
            # Pre-hydration relevance gate: same threshold, same rounded
            # cosine scores as the post-hydration DENSE_FUSION_MIN_COSINE
            # filter in search(), so the final list is IDENTICAL — but
            # rows that would be discarded anyway are never fetched.
            # Hydrating ~100 sparse chunk rows from the ~10GB store is the
            # dominant dense cost (~1.4s under disk pressure, measured);
            # with the 0.90 gate the survivors are typically 0-5.
            matches = [match for match in matches if match.score >= min_score]
        if not matches:
            return []

        # Hydrate chunk rows in ONE batched query per 500 matches (uses
        # idx_chunks_qdrant_point, schema v7) instead of N point lookups;
        # rows are then reordered to match the vector-search ranking.
        rows_by_point_id = _hydrate_chunks_by_point_ids(
            conn, [match.point_id for match in matches]
        )
        hits: list[SearchHit] = []
        for match in matches:
            row = rows_by_point_id.get(match.point_id)
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


    def get_document_text(self, document_id: str) -> dict:
        """Return a document's full indexed text, reassembled from its chunks.

        Reads the store rather than the filesystem, so it still answers for
        sources that have since moved or been deleted. This is the
        drill-down behind a compact search payload: search returns
        excerpts plus ids, and this resolves one id to the whole text.
        """
        if not self.db_path.exists():
            return {"error": "db not found"}

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT path, filename FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not row:
                return {"error": "document not found"}
            chunks = conn.execute(
                """
                SELECT chunk_index, heading_path, content
                FROM chunks WHERE document_id = ? ORDER BY chunk_index
                """,
                (document_id,),
            ).fetchall()

        # Strip the ingestion preamble, exactly as the search path does.
        # Without this the drill-down opened with a wall of
        # TRUENEX_INGESTION_METADATA JSON before the document's first line —
        # visible to anyone reading a result in full, and on short files it
        # was most of what came back.
        pieces = [
            stripped
            for chunk in chunks
            if (stripped := _strip_metadata_preamble(chunk["content"] or ""))
        ]
        content = "\n".join(pieces)
        return {
            "document_id": document_id,
            "path": row["path"],
            "filename": row["filename"],
            "chunk_count": len(chunks),
            "content_chars": len(content),
            "content": content,
            "headings": [
                chunk["heading_path"] for chunk in chunks if chunk["heading_path"]
            ],
        }

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

def _neighbour_context(
    conn: sqlite3.Connection, hit: SearchHit, span: int
) -> str:
    """The candidate's text plus its neighbouring chunks in the same document.

    Used ONLY to compute the reranker's score. The index is untouched, the
    chunks stay as they are, and the hit returned to the caller is always the
    original chunk.

    Why at read time instead of reindexing: measured 2026-08-21 on the 25 `.py`
    files targeted by the eval sets, 187 docstrings out of 194 ALREADY sit in
    the same chunk as their signature — the fragmentation two independent
    reviewers hypothesised does not occur (4%). The gain from enrichment (on
    the blind set: 0 -> 2 cases in top-5, median rank 20 -> 12) therefore comes
    from giving the cross-encoder more surface to judge, not from repairing a
    bad split. So it is assembled on the fly and nothing is migrated.
    """

    body = hit.content or ""
    if span <= 0 or not hit.document_id:
        return body
    try:
        rows = conn.execute(
            "SELECT content FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (hit.document_id,),
        ).fetchall()
    except sqlite3.Error:  # pragma: no cover - defensive
        return body
    texts = [row[0] or "" for row in rows]
    try:
        here = texts.index(body)
    except ValueError:
        return body
    window = texts[max(0, here - span) : here + span + 1]
    return "\n".join(text for text in window if text)


def _rerank_hits(
    query: str,
    first_stage: list[SearchHit],
    dense_candidates: list[SearchHit],
    config: RerankerConfig,
    conn: sqlite3.Connection | None = None,
) -> list[SearchHit]:
    """Reorder the first stage with a cross-encoder over a wider candidate set.

    The first stage decides which candidates EXIST; it never looks at a query
    and a candidate together, so it cannot decide which one answers. This adds
    that second look, over the union of what the fusion returned and the dense
    candidates it did not use.

    Effectiveness is NOT established: on the development set this step took
    2/30 to 6/30 in top-5, on the FROZEN set written by a different agent it
    took 2/30 to 2/30. See `truenex_memory.retrieval.reranker` for the full
    table before relying on it.

    Two deliberate properties:

    - the returned ``score`` is the CROSS-ENCODER's, not an RRF score, because
      the order is the cross-encoder's; the scales are not comparable and
      pretending otherwise would be worse than changing it;
    - any failure — missing dependency, unavailable model, runtime error —
      returns the first stage untouched. Reranking is an improvement, never a
      prerequisite.
    """

    # Memories are NOT reranked, and keep the positions the fusion gave them.
    #
    # The cross-encoder is trained to judge a passage against a query; a
    # curated note is a different object, and on the committed eval set a
    # blanket rerank collapsed `bug-report` from 6/6 to 2/6 — four of those
    # six cases target a memory node — while `memory-recall` held at 13/14
    # only because its queries quote their target almost verbatim, so the
    # cross-encoder happened to agree. Reranking exists to fix the ORDER OF
    # DOCUMENT CHUNKS on questions phrased without the document's words;
    # memories already have their own relevance gate and their own fusion
    # weight, and nothing measured suggests the cross-encoder ranks them
    # better than that.
    memories = [h for h in first_stage if h.memory_type != "document_chunk"]
    chunk_stage = [h for h in first_stage if h.memory_type == "document_chunk"]

    # Split the budget between the two pools instead of filling it from the
    # first stage and appending what is left. Taking the fused list first
    # exhausted the cap before a single dense candidate entered, which threw
    # away exactly the candidates the reranker exists to rescue: on the
    # paraphrase set the lexical list holds the target in 9/30 cases and the
    # union in 15/30, and the integrated version scored 4/30 against the
    # experiment's 6/30 purely because of this. Neither figure replicated
    # on the frozen set — see retrieval/reranker.py.
    half = max(1, config.candidate_limit // 2)
    candidates = list(chunk_stage[:half])
    seen = {
        (hit.source_path, _normalized_hit_content(hit.content)) for hit in candidates
    }
    for hit in dense_candidates:
        if len(candidates) >= config.candidate_limit:
            break
        key = (hit.source_path, _normalized_hit_content(hit.content))
        if key not in seen:
            seen.add(key)
            candidates.append(hit)
    # Any budget the dense pool did not use goes back to the first stage.
    for hit in chunk_stage[half:]:
        if len(candidates) >= config.candidate_limit:
            break
        key = (hit.source_path, _normalized_hit_content(hit.content))
        if key not in seen:
            seen.add(key)
            candidates.append(hit)
    if len(candidates) < 2:
        return first_stage

    texts = []
    for hit in candidates:
        body = (
            _neighbour_context(conn, hit, config.context_span)
            if conn is not None and config.context_span
            else (hit.content or "")
        )
        texts.append(f"{hit.title}\n{body}" if hit.title else body)
    scores = CrossEncoderReranker(config.model_name).score(query, texts)
    if scores is None or len(scores) != len(candidates):
        return first_stage

    # Fuse the two orderings instead of letting the reranker overwrite the
    # first stage. Replacing it outright was measured as too aggressive: on
    # the committed set the cross-encoder won 4 cases and lost 9 (docs-it
    # 10/14 -> 5/14), because its ordering is better on questions phrased
    # away from the document's words and worse where BM25 was already right.
    # RRF over (first-stage rank, reranker rank) keeps what both agree on and
    # lets neither bury a correct answer alone.
    rerank_order = [
        index
        for index, _ in sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
    ]
    rerank_rank = {index: position for position, index in enumerate(rerank_order, start=1)}
    first_rank = {}
    for position, hit in enumerate(chunk_stage, start=1):
        first_rank[(hit.source_path, _normalized_hit_content(hit.content))] = position
    # A candidate the first stage never returned is ranked just past its tail,
    # so it needs real reranker support to reach the top rather than arriving
    # there by default.
    unseen_rank = len(chunk_stage) + 1

    fused: list[tuple[float, SearchHit]] = []
    for index, hit in enumerate(candidates):
        key = (hit.source_path, _normalized_hit_content(hit.content))
        rank_a = first_rank.get(key, unseen_rank)
        rank_b = rerank_rank[index]
        if config.mode == "replace":
            fused.append((1.0 / (RRF_K + rank_b), hit))
        else:
            fused.append((1.0 / (RRF_K + rank_a) + 1.0 / (RRF_K + rank_b), hit))
    ordered = [
        replace(hit, score=round(value, 6))
        for value, hit in sorted(fused, key=lambda pair: pair[0], reverse=True)
    ]

    # Rebuild the list keeping every memory where the fusion put it, and
    # filling the remaining slots with the reranked chunks. A memory at fused
    # rank 1 therefore stays at rank 1.
    memory_slots = {
        index for index, hit in enumerate(first_stage)
        if hit.memory_type != "document_chunk"
    }
    merged: list[SearchHit] = []
    memory_iter = iter(memories)
    chunk_iter = iter(ordered)
    total = len(memories) + len(ordered)
    for index in range(total):
        if index in memory_slots:
            nxt = next(memory_iter, None)
            if nxt is not None:
                merged.append(nxt)
                continue
        nxt = next(chunk_iter, None)
        if nxt is None:
            nxt = next(memory_iter, None)
        if nxt is None:
            break
        merged.append(nxt)
    return merged


def _require_most_informative_token(
    conn: sqlite3.Connection,
    memory_hits: list[SearchHit],
    query_terms: set[str],
) -> list[SearchHit]:
    """Drop memories that miss the rarest term of the query.

    The plain overlap ratio treats every content token as equally telling,
    which on short questions lets two generic words carry a memory to rank
    1. Measured on the live store: "quali sono i passi per fare una release
    e il bump di versione" has four content terms, and a memory about
    ANOTHER project (MedDesk) that shares only `release` (df 10,195) and
    `versione` (df 1,000) clears 2/4 = 0.5 and wins the top slot, while the
    document that answers the question sits at rank 6. The term that makes
    the question specific is `bump` (df 373), and the memory does not
    contain it.

    Because the memory branch holds ~3k rows against ~200k chunks, any
    memory clearing the gate lands at rank 1 of its own list and scores
    1/61, which beats a correct chunk at rank 6-7. So the gate, not the
    weight, is where topical relevance has to be decided.

    Two deliberate escape hatches:

    - a near-verbatim match (the shape of every memory-recall query, whose
      overlap is 1.0 over 7-16 content terms) is admitted regardless, so
      curated recall cannot regress;
    - if NO surviving memory contains the rarest term, the rule is skipped
      entirely. Otherwise a typo, or a term that simply never occurs in the
      memory corpus, would silently empty the whole branch.

    PROVISIONAL. Approved on the mechanism and on one pre-registered case,
    NOT on aggregate evidence: it moved the 53-case eval by +1 (and by +2
    with the dense branch off), which a sign test puts at p = 0.125 — far
    from significant. It is kept because the defect was diagnosed and the
    expected case named BEFORE measuring, the targeted case flipped, the
    paired per-case comparison shows zero cases lost, and the change is
    reversible at no cost. Re-examine it when the eval grows: the honest
    reading today is "plausible and harmless", not "validated".

    Known uncovered case: a genuinely relevant memory with moderate
    overlap that happens to miss the rarest term, while an irrelevant one
    contains it. Neither hatch catches that, and the relevant memory is
    dropped. Document frequency also shifts as the corpus grows, so which
    term counts as rarest is not stable over time.
    """

    if len(query_terms) < 2 or not memory_hits:
        return memory_hits

    frequencies = document_frequencies(conn, query_terms)
    rarest = most_informative_tokens(frequencies)
    if not rarest:
        return memory_hits

    def carries_rarest(hit: SearchHit) -> bool:
        return bool(rarest & content_tokens(f"{hit.title} {hit.content}"))

    if not any(carries_rarest(hit) for hit in memory_hits):
        return memory_hits

    return [
        hit
        for hit in memory_hits
        if hit.score >= MEMORY_GATE_NEAR_VERBATIM_OVERLAP or carries_rarest(hit)
    ]


def _is_session_derived(source_path: str | None) -> bool:
    """True when a path addresses an agent conversation transcript.

    Those nodes are produced by global_auto_memory from `.jsonl` session
    files and addressed as `<file>.jsonl::exchange_N`.
    """
    if not source_path:
        return False
    lowered = source_path.lower()
    return "::exchange_" in lowered or lowered.endswith(".jsonl")


def _is_unvetted_session_memory(source_path: str | None, status: str | None) -> bool:
    """True for transcript-derived content nobody has vouched for.

    Raw dialogue matches query tokens as readily as curated content while
    carrying no answer, so it must stay out of results — but only while it
    is *unvetted*. Excluding it by path alone silently discarded 111
    memories that a person had explicitly approved or curated to `active`,
    and made `global auto approve` a no-op: it promotes a node that
    retrieval then throws away regardless. If a human marked it active,
    retrieval honours that; sloppy approvals are fixed by reviewing them,
    not by overriding the decision in code.
    """
    if not _is_session_derived(source_path):
        return False
    return (status or "").lower() != "active"


def _search_memories(
    conn: sqlite3.Connection,
    tokens: set[str],
    include_inactive: bool,
    project_id: str = PROJECT_ID,
    *,
    include_sessions: bool = False,
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
    # Drop function words from the query side too, so numerator and
    # denominator of the overlap ratio are measured on the same basis.
    # Shared with the gate in search(): an inline copy of this expression
    # would let the scorer and the gate disagree on what a content term is.
    query_terms = content_tokens_from(tokens)
    hits = []
    for row in rows:
        if not include_sessions and _is_unvetted_session_memory(
            row["source_path"], row["status"]
        ):
            continue
        # Score over CONTENT tokens only. The raw-token ratio was
        # inflated by function words: on "cos'e il source ledger e a cosa
        # serve" five of eight tokens are function words, so a memory
        # containing just those cleared MEMORY_FUSION_MIN_OVERLAP with no
        # topical relevance — and the resulting mass of tied scores was
        # then ordered by SQLite row order, making the top memories
        # effectively arbitrary. Near-verbatim queries are unaffected:
        # their content tokens are all present, so the ratio stays 1.0.
        overlap = query_terms & content_tokens(f"{row['title']} {row['content']}")
        score = round(len(overlap) / len(query_terms), 4) if query_terms else 0.0
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
                    memory_id=row["id"],
                )
            )
    return hits


def _search_chunks(
    conn: sqlite3.Connection,
    tokens: set[str],
    project_id: str = PROJECT_ID,
    *,
    limit: int = 100,
    scope: str | None = None,
) -> list[SearchHit]:
    if chunks_fts_available(conn):
        try:
            return _search_chunks_fts(conn, tokens, project_id, limit=limit, scope=scope)
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 chunk search failed, falling back to Python BM25: %s", exc)
    hits = _search_chunks_bm25(conn, tokens, project_id, limit=limit if not scope else limit * 8)
    if scope:
        # Il ripiego in Python non ha una condizione SQL da usare, quindi qui il
        # filtro e' successivo: si chiede un bacino piu' largo per compensare.
        needle = scope.replace(chr(92), "/").lower().strip("/")
        hits = [h for h in hits if needle in str(h.source_path or "").replace(chr(92), "/").lower()]
        hits = hits[:limit]
    return hits


def _search_chunks_fts(
    conn: sqlite3.Connection,
    tokens: set[str],
    project_id: str,
    *,
    limit: int,
    scope: str | None = None,
) -> list[SearchHit]:
    """Lexical chunk candidates, optionally restricted to one source tree.

    ``scope`` is matched against whole path SEGMENTS, case-insensitively and
    with separators normalised, and is applied INSIDE the SQL so the candidate
    budget is filled with in-scope chunks. Filtering afterwards would leave the
    budget spent on the rest of the corpus.

    Segmenti interi e non sottostringa: `truenex-memory` prendeva anche
    `truenex-memory-dev` e `truenex-memory-old`, cioe' un progetto vicino e
    SBAGLIATO — il caso peggiore, perche' la risposta e' plausibile. Chiedere a
    chi cerca di stare attento al nome non e' un rimedio: la condizione ora
    richiede che lo scope sia delimitato da separatori, quindi il vicino non
    entra e non c'e' niente da ricordare.
    """
    from truenex_memory.retrieval.scoring import source_boost

    query = _fts_or_query(tokens)
    if not query:
        return []
    scope_norm = scope.replace(chr(92), "/").lower().strip("/") if scope else None
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
          AND (
                ? IS NULL
                OR REPLACE(LOWER(d.path), '\\', '/') LIKE ?
                OR REPLACE(LOWER(d.path), '\\', '/') LIKE ?
              )
        ORDER BY fts_rank ASC
        LIMIT ?
        """,
        (
            query,
            scope_norm,
            None if scope_norm is None else f"{scope_norm}/%",
            None if scope_norm is None else f"%/{scope_norm}/%",
            limit,
        ),
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


def _normalized_hit_content(content: str) -> str:
    """Lowercase with whitespace collapsed; shared by fusion and dedup keys."""

    return " ".join(content.split()).casefold()


def _fuse_ranked_hits(
    memory_hits: list[SearchHit],
    chunk_hits: list[SearchHit],
    dense_hits: list[SearchHit] | None = None,
) -> list[SearchHit]:
    """Merge memory and chunk hits with Reciprocal Rank Fusion (RRF).

    The sources produce scores on incomparable scales (memories:
    token-overlap ratio in 0.0-1.0; lexical chunks: rescaled BM25 in the
    hundreds; dense chunks: cosine 0.0-1.0), so they are ranked
    independently and merged by *position*, not by raw score::

        fused(hit) = sum over sources of weight_source / (RRF_K + rank_in_source)

    Ranks are 1-based within each source after sorting by that source's own
    score, descending. ``RRF_K`` (60) is the standard smoothing constant; it
    damps the gap between adjacent top ranks. ``MEMORY_SOURCE_WEIGHT`` (1.5)
    versus ``CHUNK_SOURCE_WEIGHT`` (1.0) encodes that memories are curated
    knowledge written explicitly by an agent or a person, so at equal rank
    within their own list they outrank document chunks.
    ``DENSE_SOURCE_WEIGHT`` (0.9) lets semantic candidates support lexical
    ones without overwhelming them.

    The optional third source (``dense_hits``) carries the SAME chunk
    identity as the lexical source (``document_chunk`` type, same title and
    stripped content), so a chunk found by BOTH rankers sums its RRF
    contributions — the intended "corroborated by semantics" boost. This
    works because identity is keyed on ``(memory_type, source_path or
    document_id, title, normalized_content)`` and both rankers build hits
    from the same chunk rows. Distinct chunks of the same file and distinct
    memories sharing a first line never collapse into one another.

    Exposed score scale: each returned hit carries its RRF score in
    ``score``, rounded to 6 decimals, on a single small positive scale
    where higher is better.  The value below is the maximum for a hit
    appearing *once per list*; it is NOT a hard bound, because true
    duplicates (same normalized content, e.g. mirrored copies, or a chunk
    found by both lexical and dense rankers) sum their RRF contributions
    and can exceed it::

        (MEMORY_SOURCE_WEIGHT + CHUNK_SOURCE_WEIGHT + DENSE_SOURCE_WEIGHT) / (RRF_K + 1)
        = ~0.055738

    These scores are NOT comparable with scores returned by versions
    before the RRF fusion (which mixed raw memory ratios and raw BM25
    values in one field).

    Known limitation: the legacy fallback in ``MemoryRepository.search()``
    (dense-only, when lexical finds nothing and no dense candidates join
    the fusion) exposes raw cosine scores on a different scale; those
    results do not go through this fusion.
    """

    scores: dict[tuple[str, str, str, str], float] = {}
    representatives: dict[tuple[str, str, str, str], SearchHit] = {}

    def _accumulate(hits: list[SearchHit], weight: float) -> None:
        ranked = sorted(hits, key=lambda item: item.score, reverse=True)
        # Within ONE source an identity contributes once, at its best rank.
        # Summing every occurrence conflated two opposite things: the same
        # chunk found by both the lexical and the dense ranker (real
        # corroboration — the sum ACROSS sources still rewards it) and the
        # same content repeated inside a single source's results, which is
        # one piece of evidence counted N times.
        #
        # Measured on the live store: a Cursor chat export of 2,559 chunks
        # containing 399 groups of byte-identical chunks (the largest being
        # 60 copies of a two-token `# =====` separator) accumulated ~6x the
        # score of any genuine answer and held rank 1 on four of the twelve
        # failing documentation queries, pushing the real target to rank 10,
        # 11 and 23.
        best_rank: dict[tuple[str, str, str, str], int] = {}
        for rank, hit in enumerate(ranked, start=1):
            key = (
                hit.memory_type,
                hit.source_path or hit.document_id or "",
                hit.title,
                _normalized_hit_content(hit.content),
            )
            if key not in best_rank:
                best_rank[key] = rank
            representatives.setdefault(key, hit)
        for key, rank in best_rank.items():
            scores[key] = scores.get(key, 0.0) + weight / (RRF_K + rank)

    _accumulate(memory_hits, MEMORY_SOURCE_WEIGHT)
    _accumulate(chunk_hits, CHUNK_SOURCE_WEIGHT)
    if dense_hits:
        _accumulate(dense_hits, DENSE_SOURCE_WEIGHT)

    merged = [
        replace(representatives[key], score=round(score, 6))
        for key, score in scores.items()
    ]
    merged.sort(key=lambda item: item.score, reverse=True)
    return merged


def _deduplicate_search_hits(
    hits: list[SearchHit], *, max_per_document: int | None = None
) -> list[SearchHit]:
    """Keep the highest-ranked copy of equivalent indexed content.

    ``max_per_document`` additionally caps how many chunks of the SAME
    document may appear. Content-level deduplication does not catch this:
    three DIFFERENT chunks of one file are three distinct pieces of content
    and all survive, so a single well-matching document can occupy most of
    the answer. Observed on a real question ("come preparo due computer per
    scambiarsi il lavoro tramite Git con chiavi SSH"): the same
    `docs/git-bridge-setup.md` held all three top positions, spending two of
    five slots on nothing new.

    The cap applies to documents only. Memories have no document identity
    and are never grouped.
    """

    unique: list[SearchHit] = []
    seen: set[tuple[str, str]] = set()
    per_document: dict[str, int] = {}
    for hit in hits:
        normalized_content = _normalized_hit_content(hit.content)
        key = (hit.memory_type, normalized_content)
        if normalized_content and key in seen:
            continue
        if max_per_document is not None and hit.memory_type == "document_chunk":
            identity = hit.document_id or hit.source_path
            if identity:
                if per_document.get(identity, 0) >= max_per_document:
                    continue
                per_document[identity] = per_document.get(identity, 0) + 1
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


_HYDRATION_BATCH_SIZE = 500


def _hydrate_chunks_by_point_ids(
    conn: sqlite3.Connection, point_ids: list[str]
) -> dict[str, sqlite3.Row]:
    """Fetch chunk rows for vector matches in batches of 500.

    Replaces the old one-query-per-match hydration (N round-trips, each a
    full scan before schema v7). Uses ``idx_chunks_qdrant_point``; the
    ledger filter (``allowed``) is applied here so blocked sources never
    hydrate, keeping parity with the legacy per-match query. Returns a
    ``point_id -> row`` map; callers reorder by vector-search ranking.
    """

    rows_by_point_id: dict[str, sqlite3.Row] = {}
    for offset in range(0, len(point_ids), _HYDRATION_BATCH_SIZE):
        batch = point_ids[offset : offset + _HYDRATION_BATCH_SIZE]
        placeholders = ", ".join("?" for _ in batch)
        rows = conn.execute(
            f"""
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
            WHERE c.qdrant_point_id IN ({placeholders})
              AND COALESCE(sl.allowed, 1) = 1
            """,
            batch,
        ).fetchall()
        for row in rows:
            rows_by_point_id[str(row["qdrant_point_id"])] = row
    return rows_by_point_id


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
        # Tolerate rows read before the column upgrade ran.
        superseded_by=(
            row["superseded_by"] if "superseded_by" in row.keys() else None
        ),
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

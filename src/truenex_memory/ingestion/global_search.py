"""Read-only keyword search for the Truenex Memory global store."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
import re
import sqlite3

from truenex_memory.retrieval.fusion import (
    CHUNK_SOURCE_WEIGHT,
    MEMORY_FUSION_MIN_OVERLAP,
    MEMORY_SOURCE_WEIGHT,
    reciprocal_rank_fusion,
)
from truenex_memory.store.sqlite import chunks_fts_available


DEFAULT_GLOBAL_SEARCH_LIMIT = 10
DEFAULT_EXCERPT_CHARS = 320
ACTIVE_MEMORY_STATUSES = ("active", "unverified")
EXCLUDED_LEDGER_STATUSES = ("missing", "skipped")
METADATA_MARKER = "TRUENEX_INGESTION_METADATA"
GLOBAL_SEARCH_KINDS = frozenset({"all", "memory", "chunks"})
# Marker exposed in JSON reports so consumers know result scores are
# Reciprocal Rank Fusion scores (not raw pre-RRF mixed-scale values).
SCORE_SCALE_MARKER = "rrf-k60"


@dataclass(frozen=True)
class GlobalSearchHit:
    """One read-only global search result."""

    id: str
    kind: str
    title: str
    content: str
    content_excerpt: str
    source_path: str | None
    heading_path: str | None
    memory_type: str
    status: str
    score: float
    source_kind: str | None = None
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "content_excerpt": self.content_excerpt,
            "source_path": self.source_path,
            "heading_path": self.heading_path,
            "memory_type": self.memory_type,
            "status": self.status,
            "score": self.score,
            "source_kind": self.source_kind,
            "source_document_id": self.source_document_id,
            "source_chunk_id": self.source_chunk_id,
            "confidence": self.confidence,
        }


@dataclass
class GlobalSearchReport:
    """Read-only keyword search report for global memory."""

    query: str
    db_path: str
    db_exists: bool
    top_k: int = DEFAULT_GLOBAL_SEARCH_LIMIT
    include_inactive: bool = False
    kind_filter: str = "all"
    result_count: int = 0
    results: list[GlobalSearchHit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "db_path": self.db_path,
            "db_exists": self.db_exists,
            "top_k": self.top_k,
            "include_inactive": self.include_inactive,
            "kind_filter": self.kind_filter,
            "score_scale": SCORE_SCALE_MARKER,
            "result_count": self.result_count,
            "results": [item.to_dict() for item in self.results],
            "warnings": self.warnings,
        }


def build_global_search(
    db_path: Path,
    query: str,
    *,
    top_k: int = DEFAULT_GLOBAL_SEARCH_LIMIT,
    include_inactive: bool = False,
    kind_filter: str = "all",
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
) -> GlobalSearchReport:
    """Search the global SQLite store without creating or mutating anything.

    Memory nodes and document chunks are ranked independently and merged
    with Reciprocal Rank Fusion (see
    `truenex_memory.retrieval.fusion.reciprocal_rank_fusion`): their raw
    scores live on incomparable scales (memories 0-10, chunks BM25 in the
    hundreds on large indexes), so merging by raw score would always bury
    memories below chunks.  The exposed ``score`` is the RRF score: a
    single small positive scale, theoretical maximum
    ``(MEMORY_SOURCE_WEIGHT + CHUNK_SOURCE_WEIGHT) / (RRF_K + 1) ≈ 0.041``,
    higher is better, NOT comparable with scores returned before the RRF
    fusion.  With ``kind_filter="memory"`` or ``"chunks"`` a single source
    is active and the fusion degenerates to that source's own ranking.
    """
    if top_k < 1:
        raise ValueError("top_k must be greater than zero")
    if excerpt_chars < 80:
        raise ValueError("excerpt_chars must be at least 80")
    if kind_filter not in GLOBAL_SEARCH_KINDS:
        expected = ", ".join(sorted(GLOBAL_SEARCH_KINDS))
        raise ValueError(f"invalid kind_filter {kind_filter!r}; expected one of {expected}")

    report = GlobalSearchReport(
        query=query,
        db_path=str(db_path),
        db_exists=db_path.exists(),
        top_k=top_k,
        include_inactive=include_inactive,
        kind_filter=kind_filter,
    )
    tokens = tokenize_set(query)
    if not tokens:
        report.warnings.append("query has no searchable tokens")
        return report
    if not db_path.exists():
        report.warnings.append("database not found")
        return report

    try:
        conn = _connect_readonly(db_path)
    except Exception:
        report.warnings.append("database exists but cannot be opened read-only")
        return report

    try:
        memory_hits: list[GlobalSearchHit] = []
        if kind_filter in ("all", "memory") and _table_exists(conn, "memory_nodes"):
            memory_hits = _search_memory_nodes(conn, tokens, include_inactive, excerpt_chars)
        elif kind_filter in ("all", "memory"):
            report.warnings.append("memory_nodes table not found")

        chunk_hits: list[GlobalSearchHit] = []
        if (
            kind_filter in ("all", "chunks")
            and _table_exists(conn, "chunks")
            and _table_exists(conn, "documents")
        ):
            chunk_hits = _search_chunks(conn, tokens, excerpt_chars, top_k=top_k)
        elif kind_filter in ("all", "chunks"):
            report.warnings.append("documents/chunks tables not found")

        # Filter each source before fusion: memory scores (0-10) and chunk
        # BM25 scores (hundreds) are not comparable, so they must be merged
        # by rank (RRF), never by raw score.
        memory_hits = [hit for hit in memory_hits if _is_searchable_source_path(hit.source_path)]
        chunk_hits = [hit for hit in chunk_hits if _is_searchable_source_path(hit.source_path)]
        # Same pre-fusion memory relevance gate as the MCP path
        # (`MemoryRepository.search`): RRF ignores raw scores, so without a
        # gate a long tail of single-token memories saturates the fused
        # top_k and hides every document chunk. Memory scores here are the
        # overlap ratio scaled 0-10, hence the *10.0.
        # The gate applies only when chunk evidence exists: a weak memory
        # that is the ONLY lexical evidence must be kept, not discarded.
        # Known divergence from the MCP path: `_search_memory_nodes` here
        # matches tokens against title+content+source_path, while the MCP
        # `_search_memories` matches only title+content — a memory can pass
        # the gate on this CLI path and fail it on the MCP path
        # (pre-existing divergence, known note).
        if chunk_hits:
            memory_hits = [
                hit for hit in memory_hits if hit.score >= MEMORY_FUSION_MIN_OVERLAP * 10.0
            ]
        fused = reciprocal_rank_fusion(
            [
                (MEMORY_SOURCE_WEIGHT, memory_hits),
                (CHUNK_SOURCE_WEIGHT, chunk_hits),
            ],
            key_fn=_global_hit_identity,
            score_fn=lambda hit: hit.score,
        )
        hits = [replace(hit, score=score) for score, hit in fused]
        hits.sort(key=lambda item: (-item.score, _kind_rank(item.kind), item.title, item.id))
        report.results = _deduplicate_global_hits(hits)[:top_k]
        report.result_count = len(report.results)
    except sqlite3.DatabaseError:
        report.warnings.append("database readable but global search query failed")
    finally:
        conn.close()

    return report


def format_global_search_report(report: GlobalSearchReport) -> str:
    """Format a global search report as concise terminal text."""
    lines: list[str] = [f"Global Search: {report.query}"]
    lines.append("=" * 60)
    lines.append(f"Database: {report.db_path}")
    if not report.db_exists:
        lines.append("  (not found)")
    lines.append(f"Kind: {report.kind_filter}")
    lines.append(f"Results: {report.result_count} / top_k {report.top_k}")
    if report.include_inactive:
        lines.append("Inactive memory statuses: included")

    if report.results:
        lines.append("")
        for index, item in enumerate(report.results, start=1):
            confidence = "" if item.confidence is None else f" confidence={item.confidence:.2f}"
            lines.append(
                f"{index}. {item.score:.4f} {item.title} "
                f"[{item.kind}/{item.memory_type}/{item.status}]{confidence}"
            )
            if item.source_path:
                lines.append(f"   source: {item.source_path}")
            if item.heading_path:
                lines.append(f"   heading: {item.heading_path}")
            lines.append(f"   {item.content_excerpt}")

    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in report.warnings:
            lines.append(f"  - {warning}")

    return "\n".join(lines)


def _search_memory_nodes(
    conn: sqlite3.Connection,
    tokens: set[str],
    include_inactive: bool,
    excerpt_chars: int,
) -> list[GlobalSearchHit]:
    """Memory-node candidates for global search.

    Known divergence from the MCP path: this ranker also matches tokens
    against ``source_path``, while `_search_memories` in
    `store/repository.py` matches only title+content — the two paths can
    produce different memory candidate sets for the same query.
    """
    if include_inactive:
        rows = conn.execute("SELECT * FROM memory_nodes").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM memory_nodes WHERE status IN (?, ?)",
            ACTIVE_MEMORY_STATUSES,
        ).fetchall()

    hits: list[GlobalSearchHit] = []
    for row in rows:
        content = str(row["content"] or "")
        title = str(row["title"] or "")
        text = f"{title} {content} {row['source_path'] or ''}"
        text_tokens = tokenize_set(text)
        overlap = tokens & text_tokens
        if not overlap:
            continue
        score = round(len(overlap) / len(tokens) * 10.0, 4)
        hits.append(
            GlobalSearchHit(
                id=str(row["id"]),
                kind="memory_node",
                title=title,
                content=content,
                content_excerpt=_excerpt(content, excerpt_chars),
                source_path=str(row["source_path"]) if row["source_path"] is not None else None,
                heading_path=None,
                memory_type=str(row["type"]),
                status=str(row["status"]),
                score=score,
                source_kind=str(row["source_kind"]) if row["source_kind"] is not None else None,
                source_document_id=(
                    str(row["source_document_id"])
                    if row["source_document_id"] is not None else None
                ),
                source_chunk_id=(
                    str(row["source_chunk_id"])
                    if row["source_chunk_id"] is not None else None
                ),
                confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            )
        )
    return hits


def _search_chunks(
    conn: sqlite3.Connection,
    tokens: set[str],
    excerpt_chars: int,
    *,
    top_k: int,
) -> list[GlobalSearchHit]:
    if chunks_fts_available(conn):
        try:
            return _search_chunks_fts(
                conn,
                tokens,
                excerpt_chars,
                candidate_limit=max(top_k * 20, 100),
            )
        except sqlite3.OperationalError:
            pass
    return _search_chunks_bm25(conn, tokens, excerpt_chars)


def _search_chunks_fts(
    conn: sqlite3.Connection,
    tokens: set[str],
    excerpt_chars: int,
    *,
    candidate_limit: int,
) -> list[GlobalSearchHit]:
    query = _fts_or_query(tokens)
    if not query:
        return []
    if _table_exists(conn, "source_ledger"):
        rows = conn.execute(
            """
            SELECT c.*, d.path, d.filename, NULL AS ledger_status,
                   bm25(chunks_fts, 1.0, 2.0) AS fts_rank
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            JOIN documents d ON d.id = c.document_id
            WHERE chunks_fts MATCH ?
              AND (
                NOT EXISTS (
                    SELECT 1 FROM source_ledger sl
                    WHERE sl.source_path_or_alias = d.path
                ) OR EXISTS (
                    SELECT 1 FROM source_ledger sl
                    WHERE sl.source_path_or_alias = d.path
                      AND sl.status NOT IN (?, ?)
                )
              )
            ORDER BY fts_rank ASC
            LIMIT ?
            """,
            (query, *EXCLUDED_LEDGER_STATUSES, candidate_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT c.*, d.path, d.filename, NULL AS ledger_status,
                   bm25(chunks_fts, 1.0, 2.0) AS fts_rank
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            JOIN documents d ON d.id = c.document_id
            WHERE chunks_fts MATCH ?
            ORDER BY fts_rank ASC
            LIMIT ?
            """,
            (query, candidate_limit),
        ).fetchall()

    hits: list[GlobalSearchHit] = []
    for row in rows:
        stripped_content = _strip_metadata_preamble(str(row["content"] or ""))
        source_type = row["source_type"] if "source_type" in row.keys() else None
        score = round(
            max(0.0, -float(row["fts_rank"])) * 10.0 * source_boost(source_type),
            6,
        )
        hits.append(_global_chunk_hit(row, stripped_content, excerpt_chars, score))
    return hits


def _search_chunks_bm25(
    conn: sqlite3.Connection,
    tokens: set[str],
    excerpt_chars: int,
) -> list[GlobalSearchHit]:
    if _table_exists(conn, "source_ledger"):
        rows = conn.execute(
            """
            SELECT c.*, d.path, d.filename, NULL AS ledger_status
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE NOT EXISTS (
                SELECT 1 FROM source_ledger sl
                WHERE sl.source_path_or_alias = d.path
            ) OR EXISTS (
                SELECT 1 FROM source_ledger sl
                WHERE sl.source_path_or_alias = d.path
                  AND sl.status NOT IN (?, ?)
            )
            """,
            EXCLUDED_LEDGER_STATUSES,
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT c.*, d.path, d.filename, NULL AS ledger_status
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            """
        ).fetchall()

    if not rows:
        return []

    contents_for_scoring = [_strip_metadata_preamble(str(row["content"] or "")) for row in rows]
    query_tokens = list(tokens)
    tokenized = [tokenize(c) for c in contents_for_scoring]
    bm25 = BM25(tokenized)
    scores = bm25.get_scores(query_tokens)

    hits: list[GlobalSearchHit] = []
    for row, raw_score, stripped_content in zip(rows, scores, contents_for_scoring):
        if raw_score <= 0:
            continue
        st = None
        try:
            st = row["source_type"]
        except (IndexError, KeyError):
            pass
        final_score = round(raw_score * source_boost(st), 6)
        title = str(row["heading_path"] or row["filename"] or Path(str(row["path"])).name)
        hits.append(_global_chunk_hit(row, stripped_content, excerpt_chars, final_score))
    return hits


def _global_chunk_hit(
    row: sqlite3.Row,
    content: str,
    excerpt_chars: int,
    score: float,
) -> GlobalSearchHit:
    title = str(row["heading_path"] or row["filename"] or Path(str(row["path"])).name)
    return GlobalSearchHit(
        id=str(row["id"]),
        kind="document_chunk",
        title=title,
        content=content,
        content_excerpt=_excerpt(content, excerpt_chars),
        source_path=str(row["path"]) if row["path"] is not None else None,
        heading_path=str(row["heading_path"]) if row["heading_path"] is not None else None,
        memory_type="document_chunk",
        status="active",
        score=score,
    )


def _fts_or_query(tokens: set[str]) -> str:
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in sorted(tokens))


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri_path = db_path.resolve().as_posix()
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


from truenex_memory.retrieval.scoring import BM25, tokenize, tokenize_set, source_boost


def _excerpt(content: str, max_chars: int) -> str:
    text = " ".join(content.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _strip_metadata_preamble(content: str) -> str:
    text = content.lstrip()
    if not text.startswith(METADATA_MARKER):
        return content
    parts = re.split(r"\r?\n\s*\r?\n", text, maxsplit=1)
    if len(parts) == 2:
        return parts[1].lstrip()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if index > 0 and not line.strip():
            return "\n".join(lines[index + 1 :]).lstrip()
    return content


def _is_searchable_source_path(source_path: str | None) -> bool:
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


def _global_hit_identity(hit: GlobalSearchHit) -> tuple[str, str, str, str]:
    """Fusion identity key, aligned with `store.repository._fuse_ranked_hits`.

    ``kind`` keeps the two sources disjoint (``memory_node`` vs
    ``document_chunk``), so cross-source collisions never occur; the
    normalized content keeps distinct chunks of the same file (same
    source_path, same title fallback) and distinct memories sharing a
    title separate, while true duplicates (same content, e.g. mirrored
    copies) still merge and sum their RRF contributions.
    """

    normalized_content = " ".join(hit.content.split()).casefold()
    return (hit.kind, hit.source_path or hit.id, hit.title, normalized_content)


def _deduplicate_global_hits(hits: list[GlobalSearchHit]) -> list[GlobalSearchHit]:
    unique: list[GlobalSearchHit] = []
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


def _kind_rank(kind: str) -> int:
    return 0 if kind == "memory_node" else 1

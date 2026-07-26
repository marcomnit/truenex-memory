"""Purge of `missing` source-ledger entries and their indexed content.

Unlike `global_source_health` (which is conservative and only re-marks
ledger rows), this module *deletes* data:

- `source_ledger` rows with ``status='missing'``;
- the `documents` whose ``path`` matches a purged
  ``source_path_or_alias``, together with all their `chunks`.

FTS5 rows in ``chunks_fts`` need no explicit handling: the index is an
external-content table kept in sync by the AFTER DELETE trigger
``chunks_fts_ad`` (see `truenex_memory.store.sqlite._ensure_chunks_fts`),
so deleting from `chunks` removes the corresponding FTS rows.

Safety guard: a document is never deleted when its path is still
referenced by a *non-missing* ledger row (duplicate ledger rows per path
are possible; the most recent one wins in search). Path comparisons are
case- and separator-insensitive (Windows spelling differences such as
``Docs/Shared.md`` vs ``docs/shared.md`` still protect the document).
Such documents are reported as kept, not purged.

Known limitation — Qdrant orphans: vector points referenced by
``chunks.qdrant_point_id`` are NOT deleted (external vector store).
Dense search tolerates orphan points (``row is None -> continue`` in
``_search_semantic_chunks``), but ghost slots may remain in Qdrant.
Qdrant is disabled by default, so this only matters for opt-in setups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
import sqlite3

from truenex_memory.store.sqlite import connect


# SQLite variable limit is 999 by default; stay well below it.
_DELETE_BATCH_SIZE = 500


def _batched(seq: list[str], size: int) -> Iterable[list[str]]:
    """Yield *seq* in consecutive chunks of at most *size* elements."""

    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def _normalize_path_key(path: str) -> str:
    """Case- and separator-insensitive key for internal path matching only.

    Reports and samples always show the original path spelling; this key
    exists so that Windows variants (``Docs/Shared.md`` vs
    ``docs/shared.md``) compare equal.
    """

    return path.replace("\\", "/").casefold()


@dataclass
class LedgerPurgeReport:
    """Planned or applied purge of missing ledger entries."""

    db_path: str
    dry_run: bool
    db_exists: bool = False
    missing_ledger_total: int = 0
    missing_ledger_selected: int = 0
    documents_to_delete: int = 0
    chunks_to_delete: int = 0
    documents_kept_active_reference: int = 0
    memory_nodes_affected: int = 0
    ledger_deleted: int = 0
    documents_deleted: int = 0
    chunks_deleted: int = 0
    path_filters: list[str] = field(default_factory=list)
    sample_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "db_path": self.db_path,
            "dry_run": self.dry_run,
            "db_exists": self.db_exists,
            "missing_ledger_total": self.missing_ledger_total,
            "missing_ledger_selected": self.missing_ledger_selected,
            "documents_to_delete": self.documents_to_delete,
            "chunks_to_delete": self.chunks_to_delete,
            "documents_kept_active_reference": self.documents_kept_active_reference,
            "memory_nodes_affected": self.memory_nodes_affected,
            "ledger_deleted": self.ledger_deleted,
            "documents_deleted": self.documents_deleted,
            "chunks_deleted": self.chunks_deleted,
            "path_filters": self.path_filters,
            "sample_paths": self.sample_paths,
            "warnings": self.warnings,
        }


def purge_missing_ledger_entries(
    db_path: Path,
    *,
    apply: bool = False,
    path_filters: list[str] | None = None,
    sample_limit: int = 10,
) -> LedgerPurgeReport:
    """Delete `missing` ledger rows and their documents/chunks.

    Dry-run by default: counts and samples are reported, nothing is
    deleted. ``path_filters`` are case-insensitive substrings matched
    against ``source_path_or_alias``; when provided, only missing entries
    matching at least one filter are purged.

    ``memory_nodes`` referencing purged documents are counted in
    ``memory_nodes_affected`` (informational, both in dry-run and apply)
    but are NEVER deleted: they are curated knowledge and remain valid
    even when their source document is gone.

    Qdrant vector points referenced by purged chunks are NOT deleted
    (external store, disabled by default); dense search tolerates the
    resulting orphan points.

    Apply is atomic: all deletions run in one transaction and the
    ``*_deleted`` counters are assigned to the report only after the
    commit succeeds, so a failed apply never reports phantom deletions.
    """
    filters = [item.strip().lower() for item in (path_filters or []) if item.strip()]
    report = LedgerPurgeReport(
        db_path=str(db_path),
        dry_run=not apply,
        path_filters=list(path_filters or []),
    )
    if not db_path.exists():
        report.warnings.append(f"Database not found: {db_path}")
        return report

    report.db_exists = True
    try:
        with connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            missing_rows = conn.execute(
                "SELECT source_id, source_path_or_alias FROM source_ledger WHERE status = 'missing'"
            ).fetchall()
            report.missing_ledger_total = len(missing_rows)
            if filters:
                missing_rows = [
                    row
                    for row in missing_rows
                    if any(term in (row["source_path_or_alias"] or "").lower() for term in filters)
                ]
            report.missing_ledger_selected = len(missing_rows)
            report.sample_paths = [
                str(row["source_path_or_alias"]) for row in missing_rows[:sample_limit]
            ]

            # Paths still referenced by a non-missing ledger row must keep
            # their indexed document, even if a stale missing twin exists.
            # Normalized keys: spelling differences must not defeat the guard.
            still_referenced = {
                _normalize_path_key(str(row[0]))
                for row in conn.execute(
                    "SELECT DISTINCT source_path_or_alias FROM source_ledger WHERE status != 'missing'"
                ).fetchall()
            }

            # documents.path and chunks.document_id have no indexes, so
            # per-path/per-doc lookups would be full table scans.  Load both
            # mappings once instead (one scan each).
            docs_by_path: dict[str, list[str]] = {}
            for doc_id, path in conn.execute("SELECT id, path FROM documents").fetchall():
                docs_by_path.setdefault(_normalize_path_key(str(path)), []).append(str(doc_id))
            chunk_count_by_doc: dict[str, int] = {
                str(doc_id): int(count)
                for doc_id, count in conn.execute(
                    "SELECT document_id, COUNT(*) FROM chunks GROUP BY document_id"
                ).fetchall()
            }

            purge_paths = [str(row["source_path_or_alias"]) for row in missing_rows]
            document_ids: list[str] = []
            for path in purge_paths:
                key = _normalize_path_key(path)
                if key in still_referenced:
                    report.documents_kept_active_reference += 1
                    continue
                document_ids.extend(docs_by_path.get(key, []))
            # Two missing ledger rows can share one path: dedupe so the
            # counters match what the SQL actually deletes.
            document_ids = list(dict.fromkeys(document_ids))
            report.documents_to_delete = len(document_ids)
            report.chunks_to_delete = sum(
                chunk_count_by_doc.get(doc_id, 0) for doc_id in document_ids
            )

            # Informational only: curated memory nodes that point at content
            # the purge would remove.  They are never deleted themselves.
            document_id_set = set(document_ids)
            purged_path_keys = {
                _normalize_path_key(path)
                for path in purge_paths
                if docs_by_path.get(_normalize_path_key(path))
                and _normalize_path_key(path) not in still_referenced
            }
            memory_refs = conn.execute(
                """
                SELECT source_document_id, source_path FROM memory_nodes
                WHERE source_document_id IS NOT NULL OR source_path IS NOT NULL
                """
            ).fetchall()
            report.memory_nodes_affected = sum(
                1
                for row in memory_refs
                if (row["source_document_id"] and str(row["source_document_id"]) in document_id_set)
                or (
                    row["source_path"]
                    and _normalize_path_key(str(row["source_path"])) in purged_path_keys
                )
            )

            if apply:
                pending_documents = 0
                pending_chunks = 0
                pending_ledger = 0
                for batch in _batched(document_ids, _DELETE_BATCH_SIZE):
                    # Batched IN (...) deletes: with idx_chunks_document_id
                    # each batch is an index lookup instead of a full table
                    # scan per document.  The AFTER DELETE trigger
                    # chunks_fts_ad removes the matching chunks_fts rows.
                    placeholders = ", ".join("?" for _ in batch)
                    conn.execute(
                        f"DELETE FROM chunks WHERE document_id IN ({placeholders})", batch
                    )
                    conn.execute(
                        f"DELETE FROM documents WHERE id IN ({placeholders})", batch
                    )
                    pending_documents += len(batch)
                pending_chunks = sum(
                    chunk_count_by_doc.get(doc_id, 0) for doc_id in document_ids
                )
                ledger_ids = [str(row["source_id"]) for row in missing_rows]
                for batch in _batched(ledger_ids, _DELETE_BATCH_SIZE):
                    placeholders = ", ".join("?" for _ in batch)
                    conn.execute(
                        "DELETE FROM source_ledger "
                        f"WHERE source_id IN ({placeholders}) AND status = 'missing'",
                        batch,
                    )
                    pending_ledger += len(batch)
                conn.commit()
                # Publish counters only after the commit succeeded: on a
                # mid-apply exception the `with` block rolls back and the
                # report must not claim deletions that never happened.
                report.documents_deleted = pending_documents
                report.chunks_deleted = pending_chunks
                report.ledger_deleted = pending_ledger
    except sqlite3.DatabaseError as exc:
        report.warnings.append(f"Database could not be purged: {type(exc).__name__}: {exc}")

    return report


def format_ledger_purge_report(report: LedgerPurgeReport) -> str:
    """Render a human-readable purge report."""
    mode = "dry-run" if report.dry_run else "applied"
    lines = [f"Ledger Purge Missing ({mode})", "=" * 60]
    lines.append(f"Database: {report.db_path}")
    lines.append(f"  exists: {'yes' if report.db_exists else 'no'}")
    if report.path_filters:
        lines.append(f"  path filters: {', '.join(report.path_filters)}")
    lines.append(f"  ledger missing (total): {report.missing_ledger_total}")
    lines.append(f"  ledger missing (selected): {report.missing_ledger_selected}")
    lines.append(f"  documents to delete: {report.documents_to_delete}")
    lines.append(f"  chunks to delete: {report.chunks_to_delete}")
    if report.documents_kept_active_reference:
        lines.append(
            f"  documents kept (path still referenced by non-missing ledger row): "
            f"{report.documents_kept_active_reference}"
        )
    lines.append(
        f"  memory nodes referencing purged content (kept): {report.memory_nodes_affected}"
    )
    if not report.dry_run:
        lines.append(f"  ledger rows deleted: {report.ledger_deleted}")
        lines.append(f"  documents deleted: {report.documents_deleted}")
        lines.append(f"  chunks deleted: {report.chunks_deleted}")
    if report.sample_paths:
        lines.append(f"\nSample missing paths ({len(report.sample_paths)} shown):")
        for path in report.sample_paths:
            lines.append(f"  - {path}")
    if report.warnings:
        lines.append("\nWarnings:")
        for warning in report.warnings:
            lines.append(f"  - {warning}")
    return "\n".join(lines)

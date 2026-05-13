"""Lifecycle controls for generated auto-memory nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
import uuid

from truenex_memory.core.chunker import content_hash


AUTO_MEMORY_TOMBSTONE_CONTENT = "[pruned auto memory tombstone]"
DEFAULT_PRUNE_LIMIT = 100
CURATED_AUTO_MEMORY_TYPES = frozenset({"note", "decision", "issue", "pattern"})


@dataclass(frozen=True)
class AutoMemoryLifecycleItem:
    """One generated auto-memory row touched or selected by a lifecycle command."""

    id: str
    title: str
    previous_status: str
    new_status: str | None
    source_path: str | None
    content_hash: str | None
    content_chars: int
    pruned: bool = False
    curated_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "source_path": self.source_path,
            "content_hash": self.content_hash,
            "content_chars": self.content_chars,
            "pruned": self.pruned,
            "curated_id": self.curated_id,
        }


@dataclass
class AutoMemoryLifecycleReport:
    """JSON-safe report for approve/reject/prune operations."""

    action: str
    db_path: str
    db_exists: bool
    dry_run: bool = False
    requested_id: str | None = None
    source_filter: str | None = None
    limit: int | None = None
    matched: int = 0
    changed: int = 0
    items: list[AutoMemoryLifecycleItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "db_path": self.db_path,
            "db_exists": self.db_exists,
            "dry_run": self.dry_run,
            "requested_id": self.requested_id,
            "source_filter": self.source_filter,
            "limit": self.limit,
            "matched": self.matched,
            "changed": self.changed,
            "items": [item.to_dict() for item in self.items],
            "warnings": self.warnings,
        }


def approve_auto_memory(db_path: Path, memory_id: str) -> AutoMemoryLifecycleReport:
    """Promote one generated unverified auto-memory node to active."""
    return _transition_auto_memory(
        db_path,
        memory_id=memory_id,
        action="approve",
        target_status="active",
    )


def reject_auto_memory(db_path: Path, memory_id: str) -> AutoMemoryLifecycleReport:
    """Reject one generated unverified auto-memory node by marking it obsolete."""
    return _transition_auto_memory(
        db_path,
        memory_id=memory_id,
        action="reject",
        target_status="obsolete",
    )


def promote_auto_memory(
    db_path: Path,
    memory_id: str,
    *,
    title: str,
    content: str,
    memory_type: str = "note",
    dry_run: bool = False,
) -> AutoMemoryLifecycleReport:
    """Create an active curated memory from one noisy unverified auto memory.

    This is intentionally stricter than ``approve``. The original generated row
    is marked obsolete and the curated replacement is inserted in the same
    transaction, preserving source provenance without promoting raw session
    noise as-is.
    """
    report = AutoMemoryLifecycleReport(
        action="promote",
        db_path=str(db_path),
        db_exists=db_path.exists(),
        dry_run=dry_run,
        requested_id=memory_id,
    )
    clean_title = " ".join(title.split())
    clean_content = content.strip()
    if not clean_title:
        raise ValueError("title cannot be empty")
    if not clean_content:
        raise ValueError("content cannot be empty")
    if memory_type not in CURATED_AUTO_MEMORY_TYPES:
        expected = ", ".join(sorted(CURATED_AUTO_MEMORY_TYPES))
        raise ValueError(f"invalid memory type {memory_type!r}; expected one of {expected}")
    if not db_path.exists():
        report.warnings.append("database not found")
        return report

    try:
        conn = _connect_write_existing(db_path)
    except Exception:
        report.warnings.append("database exists but cannot be opened read/write")
        return report

    try:
        if not _table_exists(conn, "memory_nodes"):
            report.warnings.append("memory_nodes table not found")
            return report

        row = conn.execute(
            """
            SELECT id, project_id, title, status, source_path, source_document_id,
                   source_chunk_id, content_hash, length(content) AS content_chars
            FROM memory_nodes
            WHERE id = ? AND project_id = 'default'
            """,
            (memory_id,),
        ).fetchone()
        if row is None:
            report.warnings.append("memory node not found")
            return report
        report.matched = 1
        report.items = [_item_from_row(row, new_status=None)]

        eligible = conn.execute(
            """
            SELECT 1
            FROM memory_nodes
            WHERE id = ?
              AND project_id = 'default'
              AND status = 'unverified'
              AND source_kind = 'auto'
              AND created_by = 'auto'
            """,
            (memory_id,),
        ).fetchone()
        if eligible is None:
            report.warnings.append("memory node is not an unverified generated auto memory")
            return report

        curated_id = f"mem_{uuid.uuid4().hex}"
        report.items = [
            _item_from_row(row, new_status="obsolete", curated_id=curated_id)
        ]
        curated_hash = content_hash(clean_content)
        if dry_run:
            duplicate = conn.execute(
                """
                SELECT id
                FROM memory_nodes
                WHERE project_id = 'default'
                  AND content_hash = ?
                  AND status = 'active'
                ORDER BY created_at, id
                LIMIT 1
                """,
                (curated_hash,),
            ).fetchone()
            if duplicate is not None:
                report.items = [_item_from_row(row, new_status=None)]
                report.warnings.append(
                    f"active memory with same curated content already exists: {duplicate['id']}"
                )
            return report

        try:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                """
                SELECT id
                FROM memory_nodes
                WHERE project_id = 'default'
                  AND content_hash = ?
                  AND status = 'active'
                ORDER BY created_at, id
                LIMIT 1
                """,
                (curated_hash,),
            ).fetchone()
            if duplicate is not None:
                conn.rollback()
                report.items = [_item_from_row(row, new_status=None)]
                report.warnings.append(
                    f"active memory with same curated content already exists: {duplicate['id']}"
                )
                return report
            insert_cursor = conn.execute(
                """
                INSERT INTO memory_nodes (
                    id, project_id, type, title, content, status, source_kind,
                    source_document_id, source_chunk_id, source_path,
                    content_hash, created_by, model_name, confidence,
                    created_at, updated_at
                )
                SELECT
                    ?, project_id, ?, ?, ?, 'active', 'curated_auto',
                    source_document_id, source_chunk_id, source_path,
                    ?, 'curated_auto', model_name, confidence,
                    datetime('now'), datetime('now')
                FROM memory_nodes
                WHERE id = ?
                  AND project_id = 'default'
                  AND status = 'unverified'
                  AND source_kind = 'auto'
                  AND created_by = 'auto'
                """,
                (
                    curated_id,
                    memory_type,
                    clean_title,
                    clean_content,
                    curated_hash,
                    memory_id,
                ),
            )
            update_cursor = conn.execute(
                """
                UPDATE memory_nodes
                SET status = 'obsolete', updated_at = datetime('now')
                WHERE id = ?
                  AND project_id = 'default'
                  AND status = 'unverified'
                  AND source_kind = 'auto'
                  AND created_by = 'auto'
                """,
                (memory_id,),
            )
            if insert_cursor.rowcount != 1 or update_cursor.rowcount != 1:
                raise sqlite3.DatabaseError("promote transaction did not touch expected rows")
            conn.commit()
        except sqlite3.DatabaseError:
            conn.rollback()
            raise
        report.changed = 2
    except sqlite3.DatabaseError:
        report.warnings.append("database readable but auto lifecycle query failed")
    finally:
        conn.close()

    return report


def prune_auto_memories(
    db_path: Path,
    *,
    source_filter: str | None = None,
    limit: int = DEFAULT_PRUNE_LIMIT,
    dry_run: bool = True,
) -> AutoMemoryLifecycleReport:
    """Compact rejected generated auto memories into tombstone rows.

    This intentionally does not hard-delete rows. Keeping the content hash gives
    the generator a local tombstone so rejected content is not recreated later.
    """
    if limit < 1:
        raise ValueError("limit must be greater than zero")

    report = AutoMemoryLifecycleReport(
        action="prune",
        db_path=str(db_path),
        db_exists=db_path.exists(),
        dry_run=dry_run,
        source_filter=source_filter,
        limit=limit,
    )
    if not db_path.exists():
        report.warnings.append("database not found")
        return report

    try:
        conn = _connect_write_existing(db_path)
    except Exception:
        report.warnings.append("database exists but cannot be opened read/write")
        return report

    try:
        if not _table_exists(conn, "memory_nodes"):
            report.warnings.append("memory_nodes table not found")
            return report

        where_sql, params = _prune_where_clause(source_filter)
        rows = conn.execute(
            f"""
            SELECT id, title, status, source_path, content_hash, length(content) AS content_chars
            FROM memory_nodes
            {where_sql}
            ORDER BY updated_at, created_at, id
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        report.matched = len(rows)
        if dry_run or not rows:
            report.items = [_item_from_row(row, new_status="obsolete") for row in rows]
            return report

        ids = [str(row["id"]) for row in rows]
        placeholders = ", ".join("?" for _ in ids)
        cursor = conn.execute(
            f"""
            UPDATE memory_nodes
            SET content = ?,
                updated_at = datetime('now')
            WHERE id IN ({placeholders})
              AND project_id = 'default'
              AND status = 'obsolete'
              AND source_kind = 'auto'
              AND created_by = 'auto'
              AND content_hash IS NOT NULL
              AND content != ?
            """,
            [AUTO_MEMORY_TOMBSTONE_CONTENT, *ids, AUTO_MEMORY_TOMBSTONE_CONTENT],
        )
        conn.commit()
        report.changed = int(cursor.rowcount)
        if report.changed != len(ids):
            report.warnings.append("some rows changed before prune completed")
        all_rows_pruned = report.changed == len(rows)
        report.items = [
            _item_from_row(row, new_status="obsolete", pruned=all_rows_pruned)
            for row in rows
        ]
    except sqlite3.DatabaseError:
        report.warnings.append("database readable but auto lifecycle query failed")
    finally:
        conn.close()

    return report


def format_auto_memory_lifecycle_report(report: AutoMemoryLifecycleReport) -> str:
    """Format lifecycle operation reports for CLI output."""
    title = f"Auto Memory {report.action.title()}"
    lines: list[str] = [title, "=" * 60]
    lines.append(f"Database: {report.db_path}")
    if not report.db_exists:
        lines.append("  (not found)")
    if report.requested_id:
        lines.append(f"Memory id: {report.requested_id}")
    if report.source_filter:
        lines.append(f"Source filter: {report.source_filter}")
    if report.limit is not None:
        lines.append(f"Limit: {report.limit}")
    if report.dry_run:
        lines.append("Mode: dry-run")
    lines.append(f"Matched: {report.matched}")
    lines.append(f"Changed: {report.changed}")

    if report.items:
        lines.append("")
        lines.append("Items:")
        for item in report.items[:20]:
            status = (
                item.previous_status
                if item.new_status is None
                else f"{item.previous_status} -> {item.new_status}"
            )
            suffix = " pruned" if item.pruned else ""
            lines.append(f"  {item.id} [{status}]{suffix}")
            if item.curated_id:
                lines.append(f"    curated: {item.curated_id}")
            lines.append(f"    title: {item.title}")
            lines.append(f"    source: {item.source_path or '(no source path)'}")
            lines.append(f"    content chars before: {item.content_chars}")

    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in report.warnings:
            lines.append(f"  - {warning}")

    return "\n".join(lines)


def _transition_auto_memory(
    db_path: Path,
    *,
    memory_id: str,
    action: str,
    target_status: str,
) -> AutoMemoryLifecycleReport:
    report = AutoMemoryLifecycleReport(
        action=action,
        db_path=str(db_path),
        db_exists=db_path.exists(),
        requested_id=memory_id,
    )
    if not db_path.exists():
        report.warnings.append("database not found")
        return report

    try:
        conn = _connect_write_existing(db_path)
    except Exception:
        report.warnings.append("database exists but cannot be opened read/write")
        return report

    try:
        if not _table_exists(conn, "memory_nodes"):
            report.warnings.append("memory_nodes table not found")
            return report

        row = conn.execute(
            """
            SELECT id, title, status, source_path, content_hash, length(content) AS content_chars
            FROM memory_nodes
            WHERE id = ? AND project_id = 'default'
            """,
            (memory_id,),
        ).fetchone()
        if row is None:
            report.warnings.append("memory node not found")
            return report
        report.matched = 1

        cursor = conn.execute(
            """
            UPDATE memory_nodes
            SET status = ?, updated_at = datetime('now')
            WHERE id = ?
              AND project_id = 'default'
              AND status = 'unverified'
              AND source_kind = 'auto'
              AND created_by = 'auto'
            """,
            (target_status, memory_id),
        )
        conn.commit()
        report.changed = int(cursor.rowcount)
        if report.changed == 0:
            report.warnings.append("memory node is not an unverified generated auto memory")
        report.items = [
            _item_from_row(
                row,
                new_status=target_status if report.changed == 1 else None,
            )
        ]
    except sqlite3.DatabaseError:
        report.warnings.append("database readable but auto lifecycle query failed")
    finally:
        conn.close()

    return report


def _item_from_row(
    row: sqlite3.Row,
    *,
    new_status: str | None,
    pruned: bool = False,
    curated_id: str | None = None,
) -> AutoMemoryLifecycleItem:
    return AutoMemoryLifecycleItem(
        id=str(row["id"]),
        title=str(row["title"]),
        previous_status=str(row["status"]),
        new_status=new_status,
        source_path=str(row["source_path"]) if row["source_path"] is not None else None,
        content_hash=str(row["content_hash"]) if row["content_hash"] is not None else None,
        content_chars=int(row["content_chars"] or 0),
        pruned=pruned,
        curated_id=curated_id,
    )


def _prune_where_clause(source_filter: str | None) -> tuple[str, list[object]]:
    where = [
        "project_id = 'default'",
        "status = 'obsolete'",
        "source_kind = 'auto'",
        "created_by = 'auto'",
        "content_hash IS NOT NULL",
        "content != ?",
    ]
    params: list[object] = [AUTO_MEMORY_TOMBSTONE_CONTENT]
    if source_filter:
        where.append("lower(coalesce(source_path, '')) LIKE ? ESCAPE '\\'")
        params.append(_like_contains(source_filter))
    return "WHERE " + " AND ".join(where), params


def _like_contains(value: str) -> str:
    escaped = (
        value.lower()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _connect_write_existing(db_path: Path) -> sqlite3.Connection:
    uri_path = db_path.resolve().as_posix()
    conn = sqlite3.connect(f"file:{uri_path}?mode=rw", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None

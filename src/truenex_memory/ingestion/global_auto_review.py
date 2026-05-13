"""Read-only review report for generated unverified auto memories."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3


DEFAULT_REVIEW_LIMIT = 20
DEFAULT_CONTENT_CHARS = 240


@dataclass(frozen=True)
class AutoMemoryReviewItem:
    """One generated memory node prepared for user review."""

    id: str
    type: str
    title: str
    content: str
    content_excerpt: str
    status: str
    source_kind: str
    source_path: str | None
    source_document_id: str | None
    source_chunk_id: str | None
    confidence: float | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "content": self.content,
            "content_excerpt": self.content_excerpt,
            "status": self.status,
            "source_kind": self.source_kind,
            "source_path": self.source_path,
            "source_document_id": self.source_document_id,
            "source_chunk_id": self.source_chunk_id,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class AutoMemorySourceSummary:
    """Count of generated auto memories for one source path."""

    source_path: str | None
    count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "count": self.count,
        }


@dataclass
class AutoMemoryReviewReport:
    """Read-only list of generated unverified auto memory nodes."""

    db_path: str
    db_exists: bool
    total: int = 0
    returned: int = 0
    limit: int = DEFAULT_REVIEW_LIMIT
    source_filter: str | None = None
    content_chars: int = DEFAULT_CONTENT_CHARS
    items: list[AutoMemoryReviewItem] = field(default_factory=list)
    by_source_path: list[AutoMemorySourceSummary] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "db_path": self.db_path,
            "db_exists": self.db_exists,
            "total": self.total,
            "returned": self.returned,
            "limit": self.limit,
            "source_filter": self.source_filter,
            "content_chars": self.content_chars,
            "items": [item.to_dict() for item in self.items],
            "by_source_path": [item.to_dict() for item in self.by_source_path],
            "warnings": self.warnings,
        }


def build_auto_memory_review(
    db_path: Path,
    *,
    limit: int = DEFAULT_REVIEW_LIMIT,
    source_filter: str | None = None,
    content_chars: int = DEFAULT_CONTENT_CHARS,
) -> AutoMemoryReviewReport:
    """Build a read-only report for generated unverified memory nodes."""
    if limit < 1:
        raise ValueError("limit must be greater than zero")
    if content_chars < 40:
        raise ValueError("content_chars must be at least 40")

    report = AutoMemoryReviewReport(
        db_path=str(db_path),
        db_exists=db_path.exists(),
        limit=limit,
        source_filter=source_filter,
        content_chars=content_chars,
    )
    if not db_path.exists():
        report.warnings.append("database not found")
        return report

    try:
        conn = _connect_readonly(db_path)
    except Exception:
        report.warnings.append("database exists but cannot be opened read-only")
        return report

    try:
        if not _table_exists(conn, "memory_nodes"):
            report.warnings.append("memory_nodes table not found")
            return report

        where_sql, params = _where_clause(source_filter)
        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM memory_nodes {where_sql}",
            params,
        ).fetchone()
        report.total = int(total_row["cnt"]) if total_row else 0

        summary_rows = conn.execute(
            f"""
            SELECT source_path, COUNT(*) AS cnt
            FROM memory_nodes
            {where_sql}
            GROUP BY source_path
            ORDER BY cnt DESC, coalesce(source_path, '')
            """,
            params,
        ).fetchall()
        report.by_source_path = [
            AutoMemorySourceSummary(
                source_path=str(row["source_path"]) if row["source_path"] is not None else None,
                count=int(row["cnt"]),
            )
            for row in summary_rows
        ]

        rows = conn.execute(
            f"""
            SELECT
              id, type, title, content, status, source_kind, source_path,
              source_document_id, source_chunk_id, confidence, created_at, updated_at
            FROM memory_nodes
            {where_sql}
            ORDER BY source_path, title, created_at, id
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        report.items = [
            AutoMemoryReviewItem(
                id=str(row["id"]),
                type=str(row["type"]),
                title=str(row["title"]),
                content=str(row["content"]),
                content_excerpt=_excerpt(str(row["content"]), content_chars),
                status=str(row["status"]),
                source_kind=str(row["source_kind"]),
                source_path=str(row["source_path"]) if row["source_path"] is not None else None,
                source_document_id=(
                    str(row["source_document_id"])
                    if row["source_document_id"] is not None else None
                ),
                source_chunk_id=(
                    str(row["source_chunk_id"])
                    if row["source_chunk_id"] is not None else None
                ),
                confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]
        report.returned = len(report.items)
    except sqlite3.DatabaseError:
        report.warnings.append("database readable but auto review query failed")
    finally:
        conn.close()

    return report


def format_auto_memory_review(report: AutoMemoryReviewReport) -> str:
    """Format generated auto memories as concise text for users."""
    lines: list[str] = ["Auto Memory Review"]
    lines.append("=" * 60)
    lines.append(f"Database: {report.db_path}")
    if not report.db_exists:
        lines.append("  (not found)")
    if report.source_filter:
        lines.append(f"Source filter: {report.source_filter}")
    lines.append(f"Total unverified auto memories: {report.total}")
    lines.append(f"Returned: {report.returned} / limit {report.limit}")

    if report.by_source_path:
        lines.append("")
        lines.append("Sources:")
        for item in report.by_source_path[:10]:
            lines.append(f"  {item.count}  {item.source_path or '(no source path)'}")

    if report.items:
        lines.append("")
        lines.append("Items:")
        for index, item in enumerate(report.items, start=1):
            confidence = "n/a" if item.confidence is None else f"{item.confidence:.2f}"
            lines.append(f"{index}. {item.id} [{item.status}/{item.type}] confidence={confidence}")
            lines.append(f"   title: {item.title}")
            lines.append(f"   source: {item.source_path or '(no source path)'}")
            lines.append(f"   content: {item.content_excerpt}")

    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in report.warnings:
            lines.append(f"  - {warning}")

    return "\n".join(lines)


def _where_clause(source_filter: str | None) -> tuple[str, list[object]]:
    where = [
        "status = 'unverified'",
        "source_kind = 'auto'",
        "created_by = 'auto'",
    ]
    params: list[object] = []
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


def _excerpt(content: str, max_chars: int) -> str:
    text = " ".join(content.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."

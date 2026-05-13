"""Source ledger domain model for incremental refresh tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3


SOURCE_LEDGER_STATUSES = frozenset({"active", "pending", "error", "missing", "skipped"})
SOURCE_LEDGER_PHASE3_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"active", "skipped", "missing", "error"}),
    "active": frozenset({"active", "skipped", "missing", "error"}),
    "skipped": frozenset({"skipped", "active", "missing", "error"}),
    "missing": frozenset({"missing", "active"}),
    "error": frozenset({"error", "active", "missing"}),
    "pending": frozenset({"active", "skipped", "missing", "error"}),
}


def is_phase3_ledger_transition_allowed(
    previous_status: str | None,
    next_status: str,
) -> bool:
    """Return whether Phase 3 refresh policy allows this ledger transition."""
    if previous_status not in SOURCE_LEDGER_PHASE3_TRANSITIONS:
        return False
    return next_status in SOURCE_LEDGER_PHASE3_TRANSITIONS[previous_status]


@dataclass(frozen=True)
class SourceLedgerRecord:
    """A row in the source_ledger table."""

    source_id: str
    source_path_or_alias: str
    project_name: str | None
    source_type: str
    parser_version: str
    content_hash: str | None
    last_modified_at: str | None
    last_indexed_at: str | None
    status: str
    error_message: str | None
    chunk_count: int
    created_at: str
    updated_at: str


def _now_sql() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_ledger_entry(
    conn: sqlite3.Connection,
    source_id: str,
    source_path_or_alias: str,
    source_type: str,
    *,
    project_name: str | None = None,
    parser_version: str = "1",
    content_hash: str | None = None,
    last_modified_at: str | None = None,
    last_indexed_at: str | None = None,
    status: str = "pending",
    error_message: str | None = None,
    chunk_count: int = 0,
) -> SourceLedgerRecord:
    if status not in SOURCE_LEDGER_STATUSES:
        raise ValueError(
            f"invalid status {status!r}, expected one of {sorted(SOURCE_LEDGER_STATUSES)}"
        )
    now = _now_sql()
    conn.execute(
        """
        INSERT INTO source_ledger (
            source_id, source_path_or_alias, project_name, source_type,
            parser_version, content_hash, last_modified_at, last_indexed_at,
            status, error_message, chunk_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            source_path_or_alias=excluded.source_path_or_alias,
            project_name=excluded.project_name,
            source_type=excluded.source_type,
            parser_version=excluded.parser_version,
            content_hash=excluded.content_hash,
            last_modified_at=excluded.last_modified_at,
            last_indexed_at=excluded.last_indexed_at,
            status=excluded.status,
            error_message=excluded.error_message,
            chunk_count=excluded.chunk_count,
            updated_at=excluded.updated_at
        """,
        (
            source_id,
            source_path_or_alias,
            project_name,
            source_type,
            parser_version,
            content_hash,
            last_modified_at,
            last_indexed_at,
            status,
            error_message,
            chunk_count,
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM source_ledger WHERE source_id = ?", (source_id,)
    ).fetchone()
    return _ledger_record_from_row(row)


def list_ledger_entries(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    source_type: str | None = None,
) -> list[SourceLedgerRecord]:
    if status is not None and status not in SOURCE_LEDGER_STATUSES:
        raise ValueError(
            f"invalid status {status!r}, expected one of {sorted(SOURCE_LEDGER_STATUSES)}"
        )
    conditions: list[str] = []
    params: list[str] = []
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if source_type is not None:
        conditions.append("source_type = ?")
        params.append(source_type)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM source_ledger{where} ORDER BY updated_at DESC",
        params,
    ).fetchall()
    return [_ledger_record_from_row(row) for row in rows]


def get_ledger_entry(
    conn: sqlite3.Connection,
    source_id: str,
) -> SourceLedgerRecord | None:
    row = conn.execute(
        "SELECT * FROM source_ledger WHERE source_id = ?", (source_id,)
    ).fetchone()
    if row is None:
        return None
    return _ledger_record_from_row(row)


def update_ledger_status(
    conn: sqlite3.Connection,
    source_id: str,
    status: str,
    *,
    error_message: str | None = None,
) -> SourceLedgerRecord:
    if status not in SOURCE_LEDGER_STATUSES:
        raise ValueError(
            f"invalid status {status!r}, expected one of {sorted(SOURCE_LEDGER_STATUSES)}"
        )
    now = _now_sql()
    cursor = conn.execute(
        """
        UPDATE source_ledger
        SET status = ?, error_message = ?, updated_at = ?
        WHERE source_id = ?
        """,
        (status, error_message, now, source_id),
    )
    if cursor.rowcount == 0:
        raise LookupError(f"source ledger entry not found: {source_id!r}")
    conn.commit()
    row = conn.execute(
        "SELECT * FROM source_ledger WHERE source_id = ?", (source_id,)
    ).fetchone()
    return _ledger_record_from_row(row)


def _ledger_record_from_row(row: sqlite3.Row) -> SourceLedgerRecord:
    return SourceLedgerRecord(
        source_id=row["source_id"],
        source_path_or_alias=row["source_path_or_alias"],
        project_name=row["project_name"],
        source_type=row["source_type"],
        parser_version=row["parser_version"],
        content_hash=row["content_hash"],
        last_modified_at=row["last_modified_at"],
        last_indexed_at=row["last_indexed_at"],
        status=row["status"],
        error_message=row["error_message"],
        chunk_count=row["chunk_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

"""Read-only global status report for the Truenex Memory global store.

Never creates directories, databases, catalog files, ledger rows, or runs
schema migrations.  Only reads what already exists.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
import json


# Report dataclass

@dataclass
class GlobalStatusReport:
    catalog_path: str
    catalog_exists: bool
    catalog_version: str | None
    catalog_total_entries: int
    catalog_confirmed_entries: int
    catalog_by_source_type: dict[str, int] = field(default_factory=dict)
    catalog_by_confirmation_status: dict[str, int] = field(default_factory=dict)

    db_path: str = ""
    db_exists: bool = False
    ledger_total_rows: int = 0
    ledger_by_status: dict[str, int] = field(default_factory=dict)
    ledger_by_source_type: dict[str, int] = field(default_factory=dict)

    indexed_documents: int = 0
    indexed_chunks: int = 0
    last_indexed_at: str | None = None

    problem_counts: dict[str, int] = field(default_factory=dict)
    recent_problems: list[dict[str, object]] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog": {
                "path": self.catalog_path,
                "exists": self.catalog_exists,
                "version": self.catalog_version,
                "total_entries": self.catalog_total_entries,
                "confirmed_entries": self.catalog_confirmed_entries,
                "by_source_type": self.catalog_by_source_type,
                "by_confirmation_status": self.catalog_by_confirmation_status,
            },
            "database": {
                "path": self.db_path,
                "exists": self.db_exists,
            },
            "ledger": {
                "total_rows": self.ledger_total_rows,
                "by_status": self.ledger_by_status,
                "by_source_type": self.ledger_by_source_type,
            },
            "indexed": {
                "documents": self.indexed_documents,
                "chunks": self.indexed_chunks,
                "last_indexed_at": self.last_indexed_at,
            },
            "problems": {
                "counts": self.problem_counts,
                "recent": self.recent_problems,
            },
            "warnings": self.warnings,
        }


# Build function

def build_global_status(
    catalog_path: Path,
    db_path: Path,
    *,
    recent_problem_limit: int = 10,
) -> GlobalStatusReport:
    """Build a read-only GlobalStatusReport.

    Never creates directories, databases, catalog files, or ledger rows.
    """
    report = GlobalStatusReport(
        catalog_path=str(catalog_path),
        catalog_exists=False,
        catalog_version=None,
        catalog_total_entries=0,
        catalog_confirmed_entries=0,
        db_path=str(db_path),
        db_exists=False,
    )

    _read_catalog(catalog_path, report)

    if db_path.exists():
        report.db_exists = True
        try:
            conn = _connect_readonly(db_path)
        except Exception:
            report.warnings.append(f"Database exists but cannot be opened: {db_path}")
        else:
            try:
                _read_ledger(conn, report, recent_problem_limit)
                _read_indexed(conn, report)
            except sqlite3.DatabaseError:
                report.warnings.append(f"Database exists but cannot be read: {db_path}")
            finally:
                conn.close()
    else:
        report.warnings.append(f"Database not found: {db_path}")

    if not report.catalog_exists:
        report.warnings.append(f"Catalog not found: {catalog_path}")

    return report


# Internal helpers

def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection.  Does NOT create the file or
    parent directories."""
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


def _read_catalog(catalog_path: Path, report: GlobalStatusReport) -> None:
    if not catalog_path.exists():
        return

    report.catalog_exists = True
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        report.warnings.append(f"Catalog exists but is invalid/unreadable: {catalog_path}")
        return
    if not isinstance(data, dict):
        report.warnings.append(f"Catalog must be a JSON object: {catalog_path}")
        return

    report.catalog_version = str(data.get("version", "1"))

    entries = data.get("entries", [])
    if not isinstance(entries, list):
        report.warnings.append(
            f"Catalog has unexpected structure (entries is not a list): {catalog_path}"
        )
        return

    report.catalog_total_entries = len(entries)

    by_source_type: dict[str, int] = defaultdict(int)
    by_confirmation_status: dict[str, int] = defaultdict(int)
    confirmed = 0

    for entry in entries:
        if not isinstance(entry, dict):
            report.warnings.append("Catalog contains non-object entries")
            continue
        st = str(entry.get("source_type", "unknown"))
        cs = str(entry.get("confirmation_status", "unknown"))
        by_source_type[st] += 1
        by_confirmation_status[cs] += 1
        if cs == "confirmed":
            confirmed += 1

    report.catalog_confirmed_entries = confirmed
    report.catalog_by_source_type = dict(by_source_type)
    report.catalog_by_confirmation_status = dict(by_confirmation_status)


def _read_ledger(
    conn: sqlite3.Connection,
    report: GlobalStatusReport,
    recent_problem_limit: int,
) -> None:
    # Check table exists
    if not _table_exists(conn, "source_ledger"):
        report.warnings.append("source_ledger table not found in database")
        return

    # Total rows
    total_row = conn.execute("SELECT COUNT(*) AS cnt FROM source_ledger").fetchone()
    report.ledger_total_rows = total_row["cnt"] if total_row else 0

    # By status
    by_status: dict[str, int] = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM source_ledger GROUP BY status"
    ):
        by_status[row["status"]] = row["cnt"]
    report.ledger_by_status = by_status

    # By source_type
    by_st: dict[str, int] = {}
    for row in conn.execute(
        "SELECT source_type, COUNT(*) AS cnt FROM source_ledger GROUP BY source_type"
    ):
        by_st[row["source_type"]] = row["cnt"]
    report.ledger_by_source_type = by_st

    # Problem counts: missing, error, skipped
    problem_statuses = ("missing", "error", "skipped")
    report.problem_counts = {
        status: by_status.get(status, 0) for status in problem_statuses
    }

    # Recent problem details
    placeholders = ",".join("?" for _ in problem_statuses)
    rows = conn.execute(
        f"SELECT source_id, source_path_or_alias, source_type, status, "
        f"error_message, last_indexed_at, updated_at "
        f"FROM source_ledger "
        f"WHERE status IN ({placeholders}) "
        f"ORDER BY updated_at DESC "
        f"LIMIT ?",
        (*problem_statuses, recent_problem_limit),
    ).fetchall()

    report.recent_problems = [
        {
            "source_id": r["source_id"],
            "source_path_or_alias": r["source_path_or_alias"],
            "source_type": r["source_type"],
            "status": r["status"],
            "error_message": r["error_message"],
            "last_indexed_at": r["last_indexed_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def _read_indexed(conn: sqlite3.Connection, report: GlobalStatusReport) -> None:
    has_documents = _table_exists(conn, "documents")
    has_chunks = _table_exists(conn, "chunks")

    if has_documents:
        doc_row = conn.execute("SELECT COUNT(*) AS cnt FROM documents").fetchone()
        report.indexed_documents = doc_row["cnt"] if doc_row else 0
    else:
        report.warnings.append("documents table not found in database")

    if has_chunks:
        ch_row = conn.execute("SELECT COUNT(*) AS cnt FROM chunks").fetchone()
        report.indexed_chunks = ch_row["cnt"] if ch_row else 0
    else:
        report.warnings.append("chunks table not found in database")

    parts: list[str] = []
    if _table_exists(conn, "source_ledger"):
        parts.append(
            "SELECT last_indexed_at FROM source_ledger WHERE last_indexed_at IS NOT NULL"
        )
    if has_documents:
        parts.append("SELECT last_indexed_at FROM documents WHERE last_indexed_at IS NOT NULL")
    if parts:
        last = conn.execute(
            "SELECT MAX(last_indexed_at) AS val FROM ("
            + " UNION ALL ".join(parts)
            + ")"
        ).fetchone()
        report.last_indexed_at = last["val"] if last and last["val"] else None


# Text formatting

def format_status_report(report: GlobalStatusReport) -> str:
    """Format a GlobalStatusReport as concise human-readable text."""
    lines: list[str] = ["Global Status"]
    lines.append("=" * 60)

    # Warnings first
    if report.warnings:
        for w in report.warnings:
            lines.append(f"[WARNING] {w}")
        lines.append("")

    # Catalog section
    lines.append(f"Catalog: {report.catalog_path}")
    if not report.catalog_exists:
        lines.append("  (not found)")
    elif report.catalog_version is None:
        lines.append("  (invalid/unreadable)")
    else:
        lines.append(f"  version: {report.catalog_version}")
        lines.append(
            f"  entries: {report.catalog_total_entries} total"
            f" / {report.catalog_confirmed_entries} confirmed"
        )
        if report.catalog_by_source_type:
            types_str = " ".join(
                f"{k}={v}" for k, v in sorted(report.catalog_by_source_type.items())
            )
            lines.append(f"  by source_type: {types_str}")
        if report.catalog_by_confirmation_status:
            cs_str = " ".join(
                f"{k}={v}" for k, v in sorted(report.catalog_by_confirmation_status.items())
            )
            lines.append(f"  by confirmation_status: {cs_str}")

    # DB section
    lines.append(f"\nDatabase: {report.db_path}")
    if not report.db_exists:
        lines.append("  (not found)")
        return "\n".join(lines)

    lines.append("  exists: yes")

    # Ledger section
    if report.warnings and any("source_ledger" in w for w in report.warnings):
        lines.append("\nLedger: (source_ledger table missing)")
    else:
        lines.append(f"\nLedger: {report.ledger_total_rows} rows")
        if report.ledger_by_status:
            st_str = " ".join(
                f"{k}={v}" for k, v in sorted(report.ledger_by_status.items())
            )
            lines.append(f"  by status: {st_str}")
        if report.ledger_by_source_type:
            ty_str = " ".join(
                f"{k}={v}" for k, v in sorted(report.ledger_by_source_type.items())
            )
            lines.append(f"  by source_type: {ty_str}")

    # Indexed section
    lines.append("\nIndexed:")
    lines.append(f"  documents: {report.indexed_documents}")
    lines.append(f"  chunks: {report.indexed_chunks}")
    if report.last_indexed_at:
        lines.append(f"  last_indexed_at: {report.last_indexed_at}")

    # Problems section
    total_problems = sum(report.problem_counts.values())
    lines.append(f"\nProblems: {total_problems}")
    if report.problem_counts:
        prob_str = " ".join(f"{k}={v}" for k, v in sorted(report.problem_counts.items()))
        lines.append(f"  {prob_str}")

    if report.recent_problems:
        lines.append(f"\nRecent problems ({len(report.recent_problems)}):")
        for p in report.recent_problems:
            err = p.get("error_message")
            err_str = f" -- {err}" if err else ""
            lines.append(
                f"  [{p['status']}] {p['source_type']}:{p['source_path_or_alias']}"
                f"{err_str}"
            )

    return "\n".join(lines)

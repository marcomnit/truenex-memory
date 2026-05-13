"""Read-only auto memory status report for Phase 3.2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from truenex_memory.ingestion.global_auto_memory import analyze_auto_memory_candidates
from truenex_memory.ingestion.global_status import (
    GlobalStatusReport,
    build_global_status,
)


@dataclass
class AutoStatusReport:
    """Global status plus Phase 3 auto-memory readiness information."""

    global_status: GlobalStatusReport
    phase: str = "3.2"
    ready: bool = False
    last_auto_run_at: str | None = None
    confirmed_sources: int = 0
    active_sources: int = 0
    missing_sources: int = 0
    error_sources: int = 0
    skipped_sources: int = 0
    actionable_skipped_sources: int = 0
    expected_skipped_sources: int = 0
    unstable_session_sources: int = 0
    transient_unstable_session_sources: int = 0
    stale_unstable_session_sources: int = 0
    unverified_memory_count: int = 0
    auto_memory_candidates: int = 0
    duplicate_skips: int = 0
    duplicate_active_skips: int = 0
    duplicate_unverified_skips: int = 0
    duplicate_rejected_skips: int = 0
    low_confidence_skips: int = 0
    non_document_skips: int = 0
    noisy_session_skips: int = 0
    skipped_reason_counts: list[dict[str, object]] = field(default_factory=list)
    unstable_session_files: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload = self.global_status.to_dict()
        payload["auto"] = {
            "phase": self.phase,
            "ready": self.ready,
            "last_auto_run_at": self.last_auto_run_at,
            "confirmed_sources": self.confirmed_sources,
            "active_sources": self.active_sources,
            "missing_sources": self.missing_sources,
            "error_sources": self.error_sources,
            "skipped_sources": self.skipped_sources,
            "actionable_skipped_sources": self.actionable_skipped_sources,
            "expected_skipped_sources": self.expected_skipped_sources,
            "unstable_session_sources": self.unstable_session_sources,
            "transient_unstable_session_sources": self.transient_unstable_session_sources,
            "stale_unstable_session_sources": self.stale_unstable_session_sources,
            "unverified_memory_count": self.unverified_memory_count,
            "auto_memory_candidates": self.auto_memory_candidates,
            "duplicate_skips": self.duplicate_skips,
            "duplicate_active_skips": self.duplicate_active_skips,
            "duplicate_unverified_skips": self.duplicate_unverified_skips,
            "duplicate_rejected_skips": self.duplicate_rejected_skips,
            "low_confidence_skips": self.low_confidence_skips,
            "non_document_skips": self.non_document_skips,
            "noisy_session_skips": self.noisy_session_skips,
            "skipped_reason_counts": self.skipped_reason_counts,
            "unstable_session_files": self.unstable_session_files,
            "warnings": self.warnings,
        }
        return payload


def build_auto_status(
    catalog_path: Path,
    db_path: Path,
    *,
    recent_problem_limit: int = 10,
    stability_seconds: int = 120,
) -> AutoStatusReport:
    """Build a read-only Phase 3 auto status report.

    This is intentionally a thin wrapper around ``build_global_status``. It
    never creates directories, databases, catalog files, or ledger rows.
    """
    global_status = build_global_status(
        catalog_path=catalog_path,
        db_path=db_path,
        recent_problem_limit=recent_problem_limit,
    )
    report = AutoStatusReport(global_status=global_status)
    report.last_auto_run_at = global_status.last_indexed_at
    report.confirmed_sources = global_status.catalog_confirmed_entries
    report.active_sources = global_status.ledger_by_status.get("active", 0)
    report.missing_sources = global_status.ledger_by_status.get("missing", 0)
    report.error_sources = global_status.ledger_by_status.get("error", 0)

    if global_status.db_exists:
        _read_auto_details(db_path, report, stability_seconds=stability_seconds)

    _evaluate_readiness(report)
    return report


def format_auto_status_report(report: AutoStatusReport) -> str:
    """Format an AutoStatusReport as concise human-readable text."""
    base = report.global_status
    lines: list[str] = ["Auto Memory Status (Phase 3.2)"]
    lines.append("=" * 60)

    if base.warnings:
        for warning in base.warnings:
            lines.append(f"[WARNING] {warning}")
        lines.append("")

    lines.append(f"Catalog: {base.catalog_path}")
    if not base.catalog_exists:
        lines.append("  (not found)")
    else:
        lines.append(
            f"  entries: {base.catalog_total_entries} total"
            f" / {base.catalog_confirmed_entries} confirmed"
        )

    lines.append(f"\nDatabase: {base.db_path}")
    lines.append("  exists: yes" if base.db_exists else "  (not found)")

    lines.append("\nAuto Readiness:")
    lines.append(f"  ready: {'yes' if report.ready else 'NO'}")
    lines.append(f"  last_auto_run_at: {report.last_auto_run_at or 'never'}")

    lines.append("\nAuto Counts:")
    lines.append(f"  confirmed_sources: {report.confirmed_sources}")
    lines.append(f"  active_sources: {report.active_sources}")
    lines.append(f"  missing_sources: {report.missing_sources}")
    lines.append(f"  error_sources: {report.error_sources}")
    lines.append(f"  skipped_sources: {report.skipped_sources}")
    lines.append(f"  actionable_skipped_sources: {report.actionable_skipped_sources}")
    lines.append(f"  expected_skipped_sources: {report.expected_skipped_sources}")
    lines.append(f"  unstable_session_sources: {report.unstable_session_sources}")
    lines.append(
        f"  transient_unstable_session_sources: "
        f"{report.transient_unstable_session_sources}"
    )
    lines.append(f"  stale_unstable_session_sources: {report.stale_unstable_session_sources}")
    lines.append(f"  unverified_memory_count: {report.unverified_memory_count}")
    lines.append(f"  auto_memory_candidates: {report.auto_memory_candidates}")
    lines.append(f"  duplicate_skips: {report.duplicate_skips}")
    lines.append(f"  duplicate_active_skips: {report.duplicate_active_skips}")
    lines.append(f"  duplicate_unverified_skips: {report.duplicate_unverified_skips}")
    lines.append(f"  duplicate_rejected_skips: {report.duplicate_rejected_skips}")
    lines.append(f"  low_confidence_skips: {report.low_confidence_skips}")
    lines.append(f"  non_document_skips: {report.non_document_skips}")
    lines.append(f"  noisy_session_skips: {report.noisy_session_skips}")

    if report.skipped_reason_counts:
        lines.append("\nSkipped Breakdown (all ledger skipped rows):")
        for item in report.skipped_reason_counts[:10]:
            lines.append(
                f"  - {item['count']}x {item['source_type']}: {item['reason']}"
            )

    if report.unstable_session_files:
        lines.append("\nUnstable Session Files:")
        for item in report.unstable_session_files[:10]:
            lines.append(
                f"  - {item['count']} skipped exchange(s): {item['path']}"
            )

    if report.warnings:
        lines.append("\nAuto Warnings:")
        for warning in report.warnings:
            lines.append(f"  - {warning}")

    return "\n".join(lines)


def _evaluate_readiness(report: AutoStatusReport) -> None:
    base = report.global_status
    warnings: list[str] = []

    if not base.catalog_exists:
        warnings.append("catalog not found; confirm sources before auto status is ready")
    elif report.confirmed_sources == 0:
        warnings.append("catalog has no confirmed sources")

    if not base.db_exists:
        warnings.append("database not found; run global auto run after confirming sources")
    elif report.active_sources == 0:
        warnings.append("no active indexed sources found")

    if report.missing_sources:
        warnings.append(f"{report.missing_sources} source(s) are missing")
    if report.error_sources:
        warnings.append(f"{report.error_sources} source(s) have indexing errors")
    other_actionable_skipped = max(
        0,
        report.actionable_skipped_sources - report.stale_unstable_session_sources,
    )
    if other_actionable_skipped:
        warnings.append(
            f"{other_actionable_skipped} actionable non-expected "
            "source(s) are skipped"
        )
    if report.stale_unstable_session_sources:
        warnings.append(
            f"{report.stale_unstable_session_sources} agent session source(s) "
            "remain unstable after the stability window"
        )

    report.warnings = warnings
    report.ready = not warnings


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


def _read_auto_details(
    db_path: Path,
    report: AutoStatusReport,
    *,
    stability_seconds: int,
) -> None:
    try:
        conn = _connect_readonly(db_path)
    except Exception:
        report.warnings.append("database exists but cannot be opened for auto details")
        return

    try:
        if not _table_exists(conn, "source_ledger"):
            return
        expected_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM source_ledger
            WHERE status = 'skipped'
              AND (
                source_type = 'server_alias'
                OR lower(coalesce(error_message, '')) LIKE '%non-local path%'
                OR lower(coalesce(error_message, '')) LIKE '%stale ledger%'
                OR lower(coalesce(error_message, '')) LIKE '%removed local source%'
                OR lower(coalesce(error_message, '')) LIKE '%disabled catalog source%'
                OR lower(coalesce(error_message, '')) LIKE '%no indexable records%'
              )
            """
        ).fetchone()
        expected_skipped = int(expected_row["cnt"]) if expected_row else 0
        report.expected_skipped_sources = expected_skipped

        skipped_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM source_ledger
            WHERE status = 'skipped'
              AND source_type != 'server_alias'
              AND lower(coalesce(error_message, '')) NOT LIKE '%non-local path%'
              AND lower(coalesce(error_message, '')) NOT LIKE '%stale ledger%'
              AND lower(coalesce(error_message, '')) NOT LIKE '%removed local source%'
              AND lower(coalesce(error_message, '')) NOT LIKE '%disabled catalog source%'
              AND lower(coalesce(error_message, '')) NOT LIKE '%no indexable records%'
            """
        ).fetchone()
        raw_non_expected_skipped = int(skipped_row["cnt"]) if skipped_row else 0

        unstable_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM source_ledger
            WHERE status = 'skipped'
              AND source_type = 'agent_session'
              AND (
                lower(coalesce(error_message, '')) LIKE '%not yet stable%'
                OR lower(coalesce(error_message, '')) LIKE '%unstable%'
              )
            """
        ).fetchone()
        report.unstable_session_sources = int(unstable_row["cnt"]) if unstable_row else 0

        reason_rows = conn.execute(
            """
            SELECT
              source_type,
              coalesce(error_message, '') AS reason,
              COUNT(*) AS cnt
            FROM source_ledger
            WHERE status = 'skipped'
            GROUP BY source_type, reason
            ORDER BY cnt DESC, source_type, reason
            LIMIT 20
            """
        ).fetchall()
        report.skipped_reason_counts = [
            {
                "source_type": row["source_type"],
                "reason": row["reason"],
                "count": int(row["cnt"]),
            }
            for row in reason_rows
        ]

        unstable_file_rows = conn.execute(
            """
            SELECT
              CASE
                WHEN instr(source_path_or_alias, '::') > 0
                THEN substr(source_path_or_alias, 1, instr(source_path_or_alias, '::') - 1)
                ELSE source_path_or_alias
              END AS path,
              COUNT(*) AS cnt,
              MIN(last_modified_at) AS first_last_modified_at,
              MAX(last_modified_at) AS last_last_modified_at,
              MAX(updated_at) AS last_updated_at
            FROM source_ledger
            WHERE status = 'skipped'
              AND source_type = 'agent_session'
              AND (
                lower(coalesce(error_message, '')) LIKE '%not yet stable%'
                OR lower(coalesce(error_message, '')) LIKE '%unstable%'
              )
            GROUP BY path
            ORDER BY cnt DESC, path
            """
        ).fetchall()
        report.unstable_session_files = [
            {
                "path": row["path"],
                "count": int(row["cnt"]),
                "first_last_modified_at": row["first_last_modified_at"],
                "last_last_modified_at": row["last_last_modified_at"],
                "last_updated_at": row["last_updated_at"],
            }
            for row in unstable_file_rows
        ]
        _classify_unstable_session_freshness(report, stability_seconds)
        report.skipped_sources = raw_non_expected_skipped
        report.actionable_skipped_sources = max(
            0,
            raw_non_expected_skipped - report.transient_unstable_session_sources,
        )

        if _table_exists(conn, "memory_nodes"):
            unverified_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM memory_nodes
                WHERE status = 'unverified'
                  AND created_by = 'auto'
                  AND source_kind = 'auto'
                """
            ).fetchone()
            report.unverified_memory_count = (
                int(unverified_row["cnt"]) if unverified_row else 0
            )

        telemetry = analyze_auto_memory_candidates(db_path)
        report.auto_memory_candidates = telemetry.candidates
        report.duplicate_skips = telemetry.duplicate_skips
        report.duplicate_active_skips = telemetry.duplicate_active
        report.duplicate_unverified_skips = telemetry.duplicate_unverified
        report.duplicate_rejected_skips = telemetry.duplicate_rejected
        report.low_confidence_skips = telemetry.low_confidence
        report.non_document_skips = telemetry.non_document_skipped
        report.noisy_session_skips = telemetry.noisy_session_skipped
    except sqlite3.DatabaseError:
        report.warnings.append("database readable but auto detail query failed")
    finally:
        conn.close()


def _classify_unstable_session_freshness(
    report: AutoStatusReport,
    stability_seconds: int,
) -> None:
    """Split unstable session skips into transient current writes and stale rows."""
    if stability_seconds <= 0:
        report.stale_unstable_session_sources = report.unstable_session_sources
        report.transient_unstable_session_sources = 0
        for item in report.unstable_session_files:
            item["freshness"] = "stale"
        return

    now = datetime.now(timezone.utc)
    transient = 0
    stale = 0
    for item in report.unstable_session_files:
        count = int(item.get("count") or 0)
        last_modified = _parse_iso_datetime(item.get("last_last_modified_at"))
        if last_modified is None:
            stale += count
            item["freshness"] = "stale"
            continue
        age_seconds = (now - last_modified).total_seconds()
        item["age_seconds"] = max(0.0, round(age_seconds, 3))
        if age_seconds < stability_seconds:
            transient += count
            item["freshness"] = "transient"
        else:
            stale += count
            item["freshness"] = "stale"

    report.transient_unstable_session_sources = transient
    report.stale_unstable_session_sources = stale


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

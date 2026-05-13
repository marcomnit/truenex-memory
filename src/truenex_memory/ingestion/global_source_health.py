"""Source catalog and ledger health review/cleanup helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from truenex_memory.discovery.source_catalog import CatalogEntry, SourceCatalog
from truenex_memory.ingestion.global_refresh import (
    _is_nonlocal_absolute_path,
    _ledger_record_belongs_to_entry,
    _parser_source_type_for_entry,
    _physical_path,
)
from truenex_memory.store.source_ledger import SourceLedgerRecord, list_ledger_entries
from truenex_memory.store.sqlite import connect


EXPECTED_SKIP_MARKERS = (
    "server_alias:",
    "non-local path:",
    "stale ledger:",
    "removed local source:",
    "disabled catalog source:",
    "no indexable records",
)


@dataclass(frozen=True)
class SourceHealthAction:
    """A planned or applied source-health cleanup action."""

    action: str
    source_id: str
    source_type: str
    path_or_alias: str
    reason: str
    previous_status: str | None = None
    next_status: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class SourceHealthReport:
    """Source catalog/ledger health report."""

    catalog_path: str
    db_path: str
    dry_run: bool
    catalog_exists: bool = False
    db_exists: bool = False
    catalog_entries: int = 0
    confirmed_entries: int = 0
    disabled_catalog_entries: int = 0
    nonlocal_catalog_entries: int = 0
    missing_catalog_entries: int = 0
    ledger_problem_entries: int = 0
    cleanup_candidates: int = 0
    catalog_changed: int = 0
    ledger_changed: int = 0
    actions: list[SourceHealthAction] = None  # type: ignore[assignment]
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.actions is None:
            self.actions = []
        if self.warnings is None:
            self.warnings = []

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog_path": self.catalog_path,
            "db_path": self.db_path,
            "dry_run": self.dry_run,
            "catalog_exists": self.catalog_exists,
            "db_exists": self.db_exists,
            "catalog_entries": self.catalog_entries,
            "confirmed_entries": self.confirmed_entries,
            "disabled_catalog_entries": self.disabled_catalog_entries,
            "nonlocal_catalog_entries": self.nonlocal_catalog_entries,
            "missing_catalog_entries": self.missing_catalog_entries,
            "ledger_problem_entries": self.ledger_problem_entries,
            "cleanup_candidates": self.cleanup_candidates,
            "catalog_changed": self.catalog_changed,
            "ledger_changed": self.ledger_changed,
            "actions": [action.to_dict() for action in self.actions],
            "warnings": self.warnings,
        }


def build_source_health(
    catalog_path: Path,
    db_path: Path,
    *,
    apply: bool = False,
    limit: int = 50,
) -> SourceHealthReport:
    """Review and optionally clean source catalog/ledger health.

    Cleanup is conservative:

    - missing local catalog entries are disabled, not deleted;
    - non-local POSIX paths on Windows are kept in the catalog but marked as
      expected skipped in the ledger;
    - stale/problem ledger rows are marked skipped with explanatory provenance;
    - no indexed documents/chunks are deleted.
    """
    report = SourceHealthReport(
        catalog_path=str(catalog_path),
        db_path=str(db_path),
        dry_run=not apply,
    )
    catalog = _load_catalog(catalog_path, report)
    confirmed = [e for e in catalog.entries if e.confirmation_status == "confirmed"]

    replacement_entries = list(catalog.entries)
    for index, entry in enumerate(catalog.entries):
        if entry.confirmation_status != "confirmed":
            continue
        if entry.source_type == "server_alias":
            continue
        if _is_nonlocal_absolute_path(entry.path_or_alias):
            report.nonlocal_catalog_entries += 1
            continue
        if not Path(entry.path_or_alias).is_absolute():
            report.missing_catalog_entries += 1
            action = SourceHealthAction(
                action="disable_catalog_entry",
                source_id=entry.id,
                source_type=entry.source_type,
                path_or_alias=entry.path_or_alias,
                previous_status=entry.confirmation_status,
                next_status="disabled",
                reason="confirmed filesystem catalog path is relative",
            )
            _add_action(report, action, limit)
            replacement_entries[index] = replace(entry, confirmation_status="disabled")
            if apply:
                report.catalog_changed += 1
            continue
        if not Path(entry.path_or_alias).exists():
            report.missing_catalog_entries += 1
            action = SourceHealthAction(
                action="disable_catalog_entry",
                source_id=entry.id,
                source_type=entry.source_type,
                path_or_alias=entry.path_or_alias,
                previous_status=entry.confirmation_status,
                next_status="disabled",
                reason="confirmed local catalog path does not exist",
            )
            _add_action(report, action, limit)
            replacement_entries[index] = replace(entry, confirmation_status="disabled")
            if apply:
                report.catalog_changed += 1

    if apply and report.catalog_changed:
        SourceCatalog(entries=replacement_entries, version=catalog.version).save(catalog_path)

    if db_path.exists():
        report.db_exists = True
        _review_ledger(
            db_path,
            replacement_entries,
            report,
            apply=apply,
            limit=limit,
        )
    else:
        report.warnings.append(f"Database not found: {db_path}")

    return report


def format_source_health_report(report: SourceHealthReport) -> str:
    mode = "dry-run" if report.dry_run else "applied"
    lines = [f"Source Health ({mode})", "=" * 60]
    lines.append(f"Catalog: {report.catalog_path}")
    lines.append(f"  exists: {'yes' if report.catalog_exists else 'no'}")
    lines.append(f"  entries: {report.catalog_entries}")
    lines.append(f"  confirmed: {report.confirmed_entries}")
    lines.append(f"  missing local confirmed entries: {report.missing_catalog_entries}")
    lines.append(f"  non-local confirmed entries: {report.nonlocal_catalog_entries}")
    lines.append(f"\nDatabase: {report.db_path}")
    lines.append(f"  exists: {'yes' if report.db_exists else 'no'}")
    lines.append(f"  ledger problem entries: {report.ledger_problem_entries}")
    lines.append(f"  cleanup candidates: {report.cleanup_candidates}")
    lines.append(f"  catalog changed: {report.catalog_changed}")
    lines.append(f"  ledger changed: {report.ledger_changed}")

    if report.warnings:
        lines.append("\nWarnings:")
        for warning in report.warnings:
            lines.append(f"  - {warning}")

    if report.actions:
        lines.append(f"\nActions ({len(report.actions)} shown):")
        for action in report.actions:
            status = ""
            if action.previous_status or action.next_status:
                status = f" [{action.previous_status or '-'} -> {action.next_status or '-'}]"
            lines.append(
                f"  - {action.action}{status}: "
                f"{action.source_type}:{action.path_or_alias} -- {action.reason}"
            )

    return "\n".join(lines)


def _load_catalog(catalog_path: Path, report: SourceHealthReport) -> SourceCatalog:
    if not catalog_path.exists():
        report.warnings.append(f"Catalog not found: {catalog_path}")
        return SourceCatalog()
    report.catalog_exists = True
    catalog = SourceCatalog.load(catalog_path)
    report.catalog_entries = len(catalog.entries)
    report.confirmed_entries = sum(
        1 for entry in catalog.entries if entry.confirmation_status == "confirmed"
    )
    return catalog


def _review_ledger(
    db_path: Path,
    catalog_entries: list[CatalogEntry],
    report: SourceHealthReport,
    *,
    apply: bool,
    limit: int,
) -> None:
    try:
        with connect(db_path) as conn:
            ledger_entries = list_ledger_entries(conn)
            problem_entries = [
                entry for entry in ledger_entries
                if entry.status in {"missing", "error", "skipped"}
            ]
            report.ledger_problem_entries = len(problem_entries)
            for ledger_entry in problem_entries:
                action = _cleanup_action_for_ledger_entry(ledger_entry, catalog_entries)
                if action is None:
                    continue
                _add_action(report, action, limit)
                if apply:
                    _apply_ledger_cleanup(conn, ledger_entry, action)
                    report.ledger_changed += 1
    except sqlite3.DatabaseError as exc:
        report.warnings.append(f"Database could not be reviewed: {type(exc).__name__}: {exc}")


def _cleanup_action_for_ledger_entry(
    ledger_entry: SourceLedgerRecord,
    catalog_entries: list[CatalogEntry],
) -> SourceHealthAction | None:
    path = _physical_path(ledger_entry.source_path_or_alias)
    if ledger_entry.status == "skipped" and _is_expected_skip(ledger_entry.error_message):
        return None

    if _is_nonlocal_absolute_path(path):
        return SourceHealthAction(
            action="mark_ledger_expected_skip",
            source_id=ledger_entry.source_id,
            source_type=ledger_entry.source_type,
            path_or_alias=ledger_entry.source_path_or_alias,
            previous_status=ledger_entry.status,
            next_status="skipped",
            reason="non-local path cannot be indexed from this local machine",
        )

    matching_catalog = _matching_catalog_entry(ledger_entry, catalog_entries)
    if matching_catalog is not None and matching_catalog.confirmation_status != "confirmed":
        return SourceHealthAction(
            action="mark_ledger_expected_skip",
            source_id=ledger_entry.source_id,
            source_type=ledger_entry.source_type,
            path_or_alias=ledger_entry.source_path_or_alias,
            previous_status=ledger_entry.status,
            next_status="skipped",
            reason="disabled catalog source should not block readiness",
        )

    if matching_catalog is None and ledger_entry.status in {"missing", "error"}:
        return SourceHealthAction(
            action="mark_ledger_expected_skip",
            source_id=ledger_entry.source_id,
            source_type=ledger_entry.source_type,
            path_or_alias=ledger_entry.source_path_or_alias,
            previous_status=ledger_entry.status,
            next_status="skipped",
            reason="stale ledger row has no confirmed catalog source",
        )

    if (
        ledger_entry.status == "missing"
        and "previously indexed source file no longer exists"
        in (ledger_entry.error_message or "").lower()
    ):
        return SourceHealthAction(
            action="mark_ledger_expected_skip",
            source_id=ledger_entry.source_id,
            source_type=ledger_entry.source_type,
            path_or_alias=ledger_entry.source_path_or_alias,
            previous_status=ledger_entry.status,
            next_status="skipped",
            reason="removed local source file is no longer active",
        )

    return None


def _matching_catalog_entry(
    ledger_entry: SourceLedgerRecord,
    catalog_entries: list[CatalogEntry],
) -> CatalogEntry | None:
    for entry in catalog_entries:
        if ledger_entry.source_id == entry.id:
            return entry
        if _ledger_source_type_matches_catalog_entry(ledger_entry.source_type, entry):
            try:
                if _ledger_record_belongs_to_entry(ledger_entry.source_path_or_alias, entry):
                    return entry
            except (OSError, ValueError):
                continue
    return None


def _ledger_source_type_matches_catalog_entry(source_type: str, entry: CatalogEntry) -> bool:
    if entry.source_type == "server_alias":
        return source_type == "server_alias"
    return source_type == _parser_source_type_for_entry(entry)


def _is_expected_skip(error_message: str | None) -> bool:
    lowered = (error_message or "").lower()
    return any(marker in lowered for marker in EXPECTED_SKIP_MARKERS)


def _apply_ledger_cleanup(
    conn: sqlite3.Connection,
    ledger_entry: SourceLedgerRecord,
    action: SourceHealthAction,
) -> None:
    error_message = _error_message_for_action(action)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE source_ledger
        SET status = 'skipped',
            error_message = ?,
            updated_at = ?
        WHERE source_id = ?
          AND status IN ('missing', 'error', 'skipped')
        """,
        (error_message, now, ledger_entry.source_id),
    )
    conn.commit()


def _error_message_for_action(action: SourceHealthAction) -> str:
    if "non-local path" in action.reason:
        return "non-local path: no local filesystem indexing"
    if "disabled catalog source" in action.reason:
        return "disabled catalog source: local path not indexed"
    if "stale ledger" in action.reason:
        return "stale ledger: no confirmed catalog source"
    if "removed local source" in action.reason:
        return "removed local source: no active local content"
    return action.reason


def _add_action(report: SourceHealthReport, action: SourceHealthAction, limit: int) -> None:
    report.cleanup_candidates += 1
    if len(report.actions) < limit:
        report.actions.append(action)

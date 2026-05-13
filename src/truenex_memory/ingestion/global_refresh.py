"""Incremental global refresh using confirmed source catalog + source ledger.

Loads confirmed catalog entries, maps them to parsers, checks the ledger
for each parsed record, and indexes only new or changed content.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time as _time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone

from truenex_memory.core.chunker import chunk_text, content_hash
from truenex_memory.discovery.source_catalog import (
    CatalogEntry,
    SourceCatalog,
    source_id,
)
from truenex_memory.ingestion.manifest import IngestionRecord
from truenex_memory.ingestion.parsers import get_parser
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.source_ledger import (
    SourceLedgerRecord,
    list_ledger_entries,
    update_ledger_status,
    upsert_ledger_entry,
)
from truenex_memory.store.sqlite import connect, initialize_schema

MAX_CHARS_BY_SOURCE_TYPE: dict[str, int] = {
    "agent_session": 600,
    "project_docs": 1200,
}
DEFAULT_MAX_CHARS = 1200
SUPPORTED_AGENT_SESSION_EXTENSIONS = {".jsonl"}


@dataclass
class RefreshReport:
    """Aggregate report for a global refresh run."""

    new: int = 0
    modified: int = 0
    unchanged: int = 0
    skipped: int = 0
    missing: int = 0
    errors: int = 0
    indexed_records: int = 0
    catalog_entries: int = 0
    refresh_skipped: bool = False
    auto_memory_candidates: int = 0
    auto_memory_created: int = 0
    auto_memory_duplicates: int = 0
    auto_memory_duplicate_active: int = 0
    auto_memory_duplicate_unverified: int = 0
    auto_memory_duplicate_rejected: int = 0
    auto_memory_low_confidence: int = 0
    auto_memory_limit_skipped: int = 0
    auto_memory_source_limit_skipped: int = 0
    auto_memory_non_document_skipped: int = 0
    auto_memory_noisy_session_skipped: int = 0
    details: list[dict[str, object]] = field(default_factory=list)

    def detail_summary(self) -> dict[str, object]:
        """Return compact counters for large per-source detail lists."""
        by_action: Counter[str] = Counter()
        by_source_type: Counter[str] = Counter()
        by_reason: Counter[str] = Counter()

        for detail in self.details:
            action = str(detail.get("action") or "unknown")
            source_type = str(detail.get("source_type") or "unknown")
            reason = detail.get("reason") or detail.get("error")
            by_action[action] += 1
            by_source_type[source_type] += 1
            if reason:
                by_reason[str(reason)] += 1

        return {
            "total": len(self.details),
            "by_action": dict(sorted(by_action.items())),
            "by_source_type": dict(sorted(by_source_type.items())),
            "top_reasons": [
                {"reason": reason, "count": count}
                for reason, count in by_reason.most_common(10)
            ],
        }

    def to_dict(self, *, detail_limit: int | None = None) -> dict[str, object]:
        details = self.details
        details_truncated = False
        if detail_limit is not None and detail_limit >= 0:
            details_truncated = len(details) > detail_limit
            details = details[:detail_limit]
        payload: dict[str, object] = {
            "new": self.new,
            "modified": self.modified,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "missing": self.missing,
            "errors": self.errors,
            "indexed_records": self.indexed_records,
            "catalog_entries": self.catalog_entries,
            "refresh_skipped": self.refresh_skipped,
            "auto_memory_candidates": self.auto_memory_candidates,
            "auto_memory_created": self.auto_memory_created,
            "auto_memory_duplicates": self.auto_memory_duplicates,
            "auto_memory_duplicate_active": self.auto_memory_duplicate_active,
            "auto_memory_duplicate_unverified": self.auto_memory_duplicate_unverified,
            "auto_memory_duplicate_rejected": self.auto_memory_duplicate_rejected,
            "auto_memory_low_confidence": self.auto_memory_low_confidence,
            "auto_memory_limit_skipped": self.auto_memory_limit_skipped,
            "auto_memory_source_limit_skipped": self.auto_memory_source_limit_skipped,
            "auto_memory_non_document_skipped": self.auto_memory_non_document_skipped,
            "auto_memory_noisy_session_skipped": self.auto_memory_noisy_session_skipped,
            "detail_summary": self.detail_summary(),
            "details": details,
            "details_total": len(self.details),
            "details_truncated": details_truncated,
        }
        if detail_limit is not None and detail_limit >= 0:
            payload["detail_limit"] = detail_limit
        return payload


@dataclass
class _RefreshRunCache:
    """Per-run caches to avoid repeated file reads and ledger queries."""

    ledger_by_source_id: dict[str, SourceLedgerRecord] = field(default_factory=dict)
    active_ledger_by_source_type: dict[str, list[SourceLedgerRecord]] = field(default_factory=dict)
    ledger_by_path_by_source_type: dict[str, dict[str, list[SourceLedgerRecord]]] = field(
        default_factory=dict
    )
    active_ledger_by_path_by_source_type: dict[str, dict[str, list[SourceLedgerRecord]]] = field(
        default_factory=dict
    )
    file_hash_by_path: dict[str, str] = field(default_factory=dict)
    file_mtime_by_path: dict[str, str] = field(default_factory=dict)

    def ledger_entry(self, source_id: str) -> SourceLedgerRecord | None:
        return self.ledger_by_source_id.get(source_id)

    def active_ledger_entries(self, source_type: str) -> list[SourceLedgerRecord]:
        return self.active_ledger_by_source_type.get(source_type, [])

    def ledger_by_physical_path(self, source_type: str) -> dict[str, list[SourceLedgerRecord]]:
        if source_type not in self.ledger_by_path_by_source_type:
            grouped: dict[str, list[SourceLedgerRecord]] = {}
            for row in self.ledger_by_source_id.values():
                if row.source_type != source_type:
                    continue
                physical_path = Path(_physical_path(row.source_path_or_alias))
                grouped.setdefault(_normalized_cache_path_key(physical_path), []).append(row)
            self.ledger_by_path_by_source_type[source_type] = grouped
        return self.ledger_by_path_by_source_type[source_type]

    def active_ledger_by_physical_path(self, source_type: str) -> dict[str, list[SourceLedgerRecord]]:
        if source_type not in self.active_ledger_by_path_by_source_type:
            grouped: dict[str, list[SourceLedgerRecord]] = {}
            for row in self.active_ledger_entries(source_type):
                physical_path = Path(_physical_path(row.source_path_or_alias))
                grouped.setdefault(_normalized_cache_path_key(physical_path), []).append(row)
            self.active_ledger_by_path_by_source_type[source_type] = grouped
        return self.active_ledger_by_path_by_source_type[source_type]

    def file_hash(self, path: Path) -> str:
        key = _cache_path_key(path)
        if key not in self.file_hash_by_path:
            self.file_hash_by_path[key] = _file_content_hash(path)
        return self.file_hash_by_path[key]

    def file_mtime(self, path: Path) -> str:
        key = _cache_path_key(path)
        if key not in self.file_mtime_by_path:
            self.file_mtime_by_path[key] = _file_mtime_iso(path)
        return self.file_mtime_by_path[key]


def _cache_path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _normalized_cache_path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(_cache_path_key(path)))


def _load_run_cache(db_path: Path) -> _RefreshRunCache:
    cache = _RefreshRunCache()
    if not db_path.exists():
        return cache
    with connect(db_path) as conn:
        rows = list_ledger_entries(conn)
    cache.ledger_by_source_id = {row.source_id: row for row in rows}
    active_by_type: dict[str, list[SourceLedgerRecord]] = {}
    for row in rows:
        if row.status == "active":
            active_by_type.setdefault(row.source_type, []).append(row)
    cache.active_ledger_by_source_type = active_by_type
    return cache


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_content_hash(path: Path) -> str:
    """SHA-256 hash of file content for ledger comparison (streaming, no RAM spike)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(8 * 1024 * 1024):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _file_mtime_iso(path: Path) -> str:
    try:
        stat = path.stat()
        return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return _now_iso()


def _is_jsonl_stable(path: Path, stability_seconds: int) -> bool:
    """Return True if a .jsonl file has not been modified recently."""
    if stability_seconds <= 0:
        return True
    try:
        mtime = path.stat().st_mtime
        return (_time.time() - mtime) >= stability_seconds
    except OSError:
        return True


def _record_text(record: IngestionRecord) -> str:
    """Build index text with metadata preamble, matching engine._record_text."""
    meta: dict[str, object] = {
        "project": record.project,
        "source_type": record.source_type,
        "source_tool": record.source_tool,
        "source_path": record.source_path,
        "session_id": record.session_id,
        "created_at": record.created_at,
        "last_modified": record.last_modified,
        "privacy_scope": record.privacy_scope,
        **record.metadata,
    }
    meta = {k: v for k, v in meta.items() if v not in (None, "")}
    preamble = json.dumps(meta, ensure_ascii=False, sort_keys=True)
    return f"TRUENEX_INGESTION_METADATA {preamble}\n\n{record.text}"


def _index_record(record: IngestionRecord, repository: MemoryRepository) -> int:
    """Index a single ingestion record. Returns chunk count or 0 if empty."""
    indexed_text = _record_text(record)
    max_chars = MAX_CHARS_BY_SOURCE_TYPE.get(record.source_type, DEFAULT_MAX_CHARS)
    chunks = chunk_text(indexed_text, max_chars=max_chars)
    if not chunks:
        return 0

    # Agent sessions produce N exchange records from the same file — each needs
    # a distinct doc_id so exchanges don't overwrite each other in upsert_document.
    exchange_index = record.metadata.get("exchange_index") if record.metadata else None
    relative_path = (
        f"{record.source_path}::exchange_{exchange_index}"
        if exchange_index is not None
        else record.source_path
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(indexed_text)
        tmp_path = Path(tmp.name)

    try:
        repository.upsert_document(
            path=tmp_path,
            relative_path=relative_path,
            chunks=chunks,
            source_type=record.source_type,
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    return len(chunks)


def _record_source_id(record: IngestionRecord) -> str:
    """Deterministic ledger source_id for a parsed record.

    For agent_session records each exchange has its own exchange_index in metadata,
    so we include it to avoid all exchanges of the same JSONL colliding on one ledger entry.
    """
    exchange_index = record.metadata.get("exchange_index") if record.metadata else None
    if exchange_index is not None:
        return source_id(record.source_type, f"{record.source_path}::exchange_{exchange_index}")
    return source_id(record.source_type, record.source_path)


def _add_detail(report: RefreshReport, detail: dict[str, object]) -> None:
    report.details.append(detail)


# Main refresh entry point

def refresh(
    catalog_path: Path,
    db_path: Path,
    *,
    dry_run: bool = False,
    stability_seconds: int = 120,
    embedder=None,
    vector_store=None,
) -> RefreshReport:
    """Run incremental global refresh.

    Args:
        catalog_path: Path to the confirmed source catalog JSON.
        db_path: Path to the SQLite database.
        dry_run: If True, report planned actions without mutating DB/ledger/index.
        stability_seconds: Skip recently-modified .jsonl files (agent sessions).
        embedder: Optional embedder for vector indexing.
        vector_store: Optional vector store for vector indexing.

    Returns:
        RefreshReport with counts and per-record details.
    """
    report = RefreshReport()

    # 1. Load catalog
    if not catalog_path.exists():
        report.errors += 1
        _add_detail(report, {
            "source_path": str(catalog_path),
            "source_type": "catalog",
            "action": "error",
            "error": f"Catalog file not found: {catalog_path}",
        })
        return report

    catalog = SourceCatalog.load(catalog_path)
    confirmed = [e for e in catalog.entries if e.confirmation_status == "confirmed"]
    report.catalog_entries = len(confirmed)

    if not confirmed:
        return report

    # 2. Initialize DB (schema)
    if not dry_run:
        with connect(db_path) as conn:
            initialize_schema(conn)

    repository = MemoryRepository(db_path, embedder=embedder, vector_store=vector_store)
    cache = _load_run_cache(db_path)

    # 3. Process each confirmed entry
    for entry in confirmed:
        _process_catalog_entry(entry, report, repository, dry_run, stability_seconds, cache)

    return report


def _process_catalog_entry(
    entry: CatalogEntry,
    report: RefreshReport,
    repository: MemoryRepository,
    dry_run: bool,
    stability_seconds: int,
    cache: _RefreshRunCache,
) -> None:
    """Process a single confirmed catalog entry."""

    # server_alias: always skip
    if entry.source_type == "server_alias":
        report.skipped += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "skipped",
            "reason": "server_alias not indexable",
        })
        if not dry_run:
            _upsert_ledger_for_catalog_entry(
                repository.db_path, entry, status="skipped",
                error_message="server_alias: no network/SSH indexing",
            )
        return

    # Resolve parser
    parser = _parser_for_entry(entry)
    if parser is None:
        report.skipped += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "skipped",
            "reason": "no parser registered for catalog source type",
        })
        if not dry_run:
            _upsert_ledger_for_catalog_entry(
                repository.db_path,
                entry,
                status="skipped",
                error_message="no parser registered for catalog source type",
            )
        return
    source_dir = _resolve_source_dir(entry)

    # Check source exists
    path = Path(entry.path_or_alias)
    if not path.exists():
        if _is_nonlocal_absolute_path(entry.path_or_alias):
            report.skipped += 1
            _add_detail(report, {
                "source_id": entry.id,
                "source_path": entry.path_or_alias,
                "source_type": entry.source_type,
                "action": "skipped",
                "reason": "non-local absolute path not indexable on this platform",
            })
            if not dry_run:
                _upsert_ledger_for_catalog_entry(
                    repository.db_path,
                    entry,
                    status="skipped",
                    error_message="non-local path: no local filesystem indexing",
                )
            return

        report.missing += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "missing",
            "reason": "source path not found",
        })
        if not dry_run:
            _upsert_ledger_for_catalog_entry(
                repository.db_path, entry, status="missing",
                error_message="source path not found",
            )
        _mark_missing_previous_records(
            entry,
            report,
            repository.db_path,
            set(),
            dry_run,
            cache,
        )
        return

    # Run parser
    project_name = entry.project_name or _infer_name(entry.path_or_alias)
    source_tool = entry.discovered_from[0] if entry.discovered_from else "global-refresh"
    privacy_scope = entry.privacy_scope

    if entry.source_type == "agent_root":
        _process_agent_root_entry(
            entry,
            report,
            repository,
            dry_run,
            stability_seconds,
            cache,
            source_dir,
            project_name,
            source_tool,
            privacy_scope,
        )
        return

    if entry.source_type in {"project_root", "document"}:
        _process_project_docs_entry(
            entry,
            report,
            repository,
            dry_run,
            stability_seconds,
            cache,
            source_dir,
            project_name,
            source_tool,
            privacy_scope,
        )
        return

    try:
        records = parser(source_dir, project_name, source_tool, privacy_scope)
    except Exception as exc:
        report.errors += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "error",
            "error": f"{type(exc).__name__}: {exc}",
        })
        if not dry_run:
            _upsert_ledger_for_catalog_entry(
                repository.db_path, entry, status="error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
        return

    if not records:
        report.skipped += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "skipped",
            "reason": "no indexable records",
        })
        if not dry_run:
            _upsert_ledger_for_catalog_entry(
                repository.db_path,
                entry,
                status="skipped",
                error_message="no indexable records",
            )

    seen_source_ids: set[str] = set()

    # Process each record from the parser
    for record in records:
        seen_source_ids.add(_record_source_id(record))
        _process_record(record, entry, report, repository, dry_run, stability_seconds, cache)

    _mark_missing_previous_records(
        entry,
        report,
        repository.db_path,
        seen_source_ids,
        dry_run,
        cache,
    )


def _parser_for_entry(entry: CatalogEntry):
    """Map catalog source_type to registered parser name."""
    if entry.source_type == "project_root":
        return get_parser("project_docs")
    if entry.source_type == "document":
        return get_parser("project_docs")
    if entry.source_type == "agent_root":
        return get_parser("agent_session")
    return None


def _parser_source_type_for_entry(entry: CatalogEntry) -> str | None:
    """Return the ledger source_type produced by the parser for a catalog entry."""
    if entry.source_type in {"project_root", "document"}:
        return "project_docs"
    if entry.source_type == "agent_root":
        return "agent_session"
    return None


def _is_nonlocal_absolute_path(path_or_alias: str) -> bool:
    """Detect POSIX absolute paths that cannot be indexed on Windows as local files."""
    if os.name != "nt":
        return False
    cleaned = path_or_alias.strip()
    if not cleaned.startswith("/") or cleaned.startswith("//"):
        return False
    return not Path(cleaned).exists()


def _resolve_source_dir(entry: CatalogEntry) -> Path:
    return Path(entry.path_or_alias)


def _infer_name(path_or_alias: str) -> str:
    cleaned = path_or_alias.strip().replace("\\", "/").rstrip("/")
    if not cleaned:
        return "unknown"
    return cleaned.rsplit("/", 1)[-1] or "unknown"


def _process_agent_root_entry(
    entry: CatalogEntry,
    report: RefreshReport,
    repository: MemoryRepository,
    dry_run: bool,
    stability_seconds: int,
    cache: _RefreshRunCache,
    source_dir: Path,
    project_name: str,
    source_tool: str,
    privacy_scope: str,
) -> None:
    parser = get_parser("agent_session")
    if parser is None:
        report.skipped += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "skipped",
            "reason": "no parser registered for catalog source type",
        })
        return

    session_files = _iter_agent_session_files(source_dir)
    if not session_files:
        report.skipped += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "skipped",
            "reason": "no indexable records",
        })
        if not dry_run:
            _upsert_ledger_for_catalog_entry(
                repository.db_path,
                entry,
                status="skipped",
                error_message="no indexable records",
            )
        _mark_missing_previous_records(
            entry,
            report,
            repository.db_path,
            set(),
            dry_run,
            cache,
        )
        return

    active_by_path = cache.active_ledger_by_physical_path("agent_session")
    ledger_by_path = cache.ledger_by_physical_path("agent_session")
    seen_source_ids: set[str] = set()
    processed_records = 0
    parse_errors = 0

    for session_file in session_files:
        mtime = cache.file_mtime(session_file)
        path_key = _normalized_cache_path_key(session_file)
        previous = active_by_path.get(path_key, [])
        all_previous = ledger_by_path.get(path_key, [])
        can_skip_full_parse = _all_previous_records_are_active(all_previous, previous)
        if can_skip_full_parse and _active_records_match_mtime(previous, mtime):
            report.unchanged += len(previous)
            processed_records += len(previous)
            seen_source_ids.update(row.source_id for row in previous)
            _add_detail(report, {
                "source_path": str(session_file.resolve()),
                "source_type": "agent_session",
                "action": "unchanged",
                "records": len(previous),
                "reason": "unchanged session file skipped before hashing",
            })
            continue

        if can_skip_full_parse:
            file_hash = cache.file_hash(session_file)
            if all(row.content_hash == file_hash for row in previous):
                report.unchanged += len(previous)
                processed_records += len(previous)
                seen_source_ids.update(row.source_id for row in previous)
                _refresh_unchanged_ledger_mtime(
                    repository.db_path,
                    previous,
                    mtime,
                    dry_run,
                )
                _add_detail(report, {
                    "source_path": str(session_file.resolve()),
                    "source_type": "agent_session",
                    "action": "unchanged",
                    "records": len(previous),
                    "reason": "unchanged session file skipped before parsing",
                })
                continue

        try:
            records = parser(session_file, project_name, source_tool, privacy_scope)
        except Exception as exc:
            report.errors += 1
            parse_errors += 1
            _add_detail(report, {
                "source_id": entry.id,
                "source_path": str(session_file.resolve()),
                "source_type": "agent_session",
                "action": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        if not records:
            report.skipped += 1
            _add_detail(report, {
                "source_path": str(session_file.resolve()),
                "source_type": "agent_session",
                "action": "skipped",
                "reason": "no indexable records",
            })
            continue

        for record in records:
            processed_records += 1
            seen_source_ids.add(_record_source_id(record))
            _process_record(record, entry, report, repository, dry_run, stability_seconds, cache)

    if processed_records == 0 and parse_errors == 0 and not dry_run:
        _upsert_ledger_for_catalog_entry(
            repository.db_path,
            entry,
            status="skipped",
            error_message="no indexable records",
        )

    _mark_missing_previous_records(
        entry,
        report,
        repository.db_path,
        seen_source_ids,
        dry_run,
        cache,
    )


def _iter_agent_session_files(source_dir: Path) -> list[Path]:
    resolved = source_dir.resolve()
    if resolved.is_file():
        if resolved.suffix.lower() in SUPPORTED_AGENT_SESSION_EXTENSIONS:
            return [resolved]
        return []
    return sorted(
        p for p in resolved.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_AGENT_SESSION_EXTENSIONS
    )


def _process_project_docs_entry(
    entry: CatalogEntry,
    report: RefreshReport,
    repository: MemoryRepository,
    dry_run: bool,
    stability_seconds: int,
    cache: _RefreshRunCache,
    source_dir: Path,
    project_name: str,
    source_tool: str,
    privacy_scope: str,
) -> None:
    parser = get_parser("project_docs")
    if parser is None:
        report.skipped += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "skipped",
            "reason": "no parser registered for catalog source type",
        })
        return

    candidates = _iter_project_doc_files(source_dir)
    if not candidates:
        report.skipped += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "skipped",
            "reason": "no indexable records",
        })
        if not dry_run:
            _upsert_ledger_for_catalog_entry(
                repository.db_path,
                entry,
                status="skipped",
                error_message="no indexable records",
            )
        _mark_missing_previous_records(
            entry,
            report,
            repository.db_path,
            set(),
            dry_run,
            cache,
        )
        return

    active_by_path = cache.active_ledger_by_physical_path("project_docs")
    seen_source_ids: set[str] = set()
    processed_records = 0
    parse_errors = 0

    for file_path in candidates:
        mtime = cache.file_mtime(file_path)
        previous = active_by_path.get(_normalized_cache_path_key(file_path), [])
        if len(previous) == 1 and _active_records_match_mtime(previous, mtime):
            ledger_record = previous[0]
            report.unchanged += 1
            processed_records += 1
            seen_source_ids.add(ledger_record.source_id)
            _add_detail(report, {
                "source_id": ledger_record.source_id,
                "source_path": ledger_record.source_path_or_alias,
                "source_type": ledger_record.source_type,
                "action": "unchanged",
                "reason": "unchanged document skipped before hashing",
            })
            continue

        file_hash = cache.file_hash(file_path)
        if len(previous) == 1 and previous[0].content_hash == file_hash:
            ledger_record = previous[0]
            report.unchanged += 1
            processed_records += 1
            seen_source_ids.add(ledger_record.source_id)
            _refresh_unchanged_ledger_mtime(
                repository.db_path,
                [ledger_record],
                mtime,
                dry_run,
            )
            _add_detail(report, {
                "source_id": ledger_record.source_id,
                "source_path": ledger_record.source_path_or_alias,
                "source_type": ledger_record.source_type,
                "action": "unchanged",
                "reason": "unchanged document skipped before parsing",
            })
            continue

        try:
            records = parser(file_path, project_name, source_tool, privacy_scope)
        except Exception as exc:
            report.errors += 1
            parse_errors += 1
            _add_detail(report, {
                "source_id": entry.id,
                "source_path": str(file_path.resolve()),
                "source_type": "project_docs",
                "action": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        if not records:
            continue

        for record in records:
            processed_records += 1
            seen_source_ids.add(_record_source_id(record))
            _process_record(record, entry, report, repository, dry_run, stability_seconds, cache)

    if processed_records == 0 and parse_errors == 0:
        report.skipped += 1
        _add_detail(report, {
            "source_id": entry.id,
            "source_path": entry.path_or_alias,
            "source_type": entry.source_type,
            "action": "skipped",
            "reason": "no indexable records",
        })
        if not dry_run:
            _upsert_ledger_for_catalog_entry(
                repository.db_path,
                entry,
                status="skipped",
                error_message="no indexable records",
            )

    _mark_missing_previous_records(
        entry,
        report,
        repository.db_path,
        seen_source_ids,
        dry_run,
        cache,
    )


def _iter_project_doc_files(source_dir: Path) -> list[Path]:
    from truenex_memory.ingestion.parsers.text_docs import (
        EXCLUDED_FILENAMES,
        INDEX_EXTENSIONS,
        _iter_candidate_files,
    )

    resolved = source_dir.resolve()
    if resolved.is_file():
        if (
            resolved.suffix.lower() in INDEX_EXTENSIONS
            and resolved.name not in EXCLUDED_FILENAMES
        ):
            return [resolved]
        return []
    return [
        p for p in _iter_candidate_files(resolved)
        if p.suffix.lower() in INDEX_EXTENSIONS
        and p.name not in EXCLUDED_FILENAMES
    ]


def _active_records_match_mtime(
    records: list[SourceLedgerRecord],
    last_modified_at: str,
) -> bool:
    return bool(records) and all(
        row.content_hash and row.last_modified_at == last_modified_at
        for row in records
    )


def _all_previous_records_are_active(
    all_previous: list[SourceLedgerRecord],
    active_previous: list[SourceLedgerRecord],
) -> bool:
    if not all_previous or len(all_previous) != len(active_previous):
        return False
    return all(row.status == "active" for row in all_previous)


def _refresh_unchanged_ledger_mtime(
    db_path: Path,
    records: list[SourceLedgerRecord],
    last_modified_at: str,
    dry_run: bool,
) -> None:
    if dry_run or not records:
        return
    with connect(db_path) as conn:
        initialize_schema(conn)
        for row in records:
            upsert_ledger_entry(
                conn,
                row.source_id,
                row.source_path_or_alias,
                row.source_type,
                project_name=row.project_name,
                parser_version=row.parser_version,
                content_hash=row.content_hash,
                last_modified_at=last_modified_at,
                last_indexed_at=row.last_indexed_at,
                status=row.status,
                error_message=row.error_message,
                chunk_count=row.chunk_count,
            )


def _process_record(
    record: IngestionRecord,
    entry: CatalogEntry,
    report: RefreshReport,
    repository: MemoryRepository,
    dry_run: bool,
    stability_seconds: int,
    cache: _RefreshRunCache,
) -> None:
    """Check ledger for a single parsed record and index if new/changed."""

    file_path = Path(record.source_path)
    rec_source_id = _record_source_id(record)
    file_hash = cache.file_hash(file_path)
    mtime = cache.file_mtime(file_path)

    # Check ledger before stability handling. If a JSONL session already has a
    # previous active version, an unstable write must leave that version active.
    existing = cache.ledger_entry(rec_source_id)

    # Stability check for .jsonl agent sessions
    if record.source_type == "agent_session" and file_path.suffix.lower() == ".jsonl":
        if not _is_jsonl_stable(file_path, stability_seconds):
            report.skipped += 1
            _add_detail(report, {
                "source_id": rec_source_id,
                "source_path": record.source_path,
                "source_type": record.source_type,
                "action": "skipped",
                "reason": "JSONL modified recently, not yet stable",
            })
            if not dry_run and (existing is None or existing.status != "active"):
                _upsert_record_ledger(
                    repository.db_path, rec_source_id, record, entry,
                    status="skipped", content_hash=file_hash,
                    last_modified_at=mtime, chunk_count=0,
                    error_message="JSONL modified recently, not yet stable",
                )
            return

    if existing is None:
        # New record
        if not file_path.exists():
            report.missing += 1
            _add_detail(report, {
                "source_id": rec_source_id,
                "source_path": record.source_path,
                "source_type": record.source_type,
                "action": "missing",
            })
            if not dry_run:
                _upsert_record_ledger(
                    repository.db_path, rec_source_id, record, entry,
                    status="missing", content_hash="",
                    last_modified_at=mtime, chunk_count=0,
                    error_message="source file not found",
                )
        else:
            if not dry_run:
                try:
                    chunk_count = _index_record(record, repository)
                except Exception as exc:
                    _record_index_error(
                        repository.db_path, rec_source_id, record, entry,
                        report, existing=None, content_hash=file_hash,
                        last_modified_at=mtime, error=exc,
                    )
                    return
                report.indexed_records += 1
                _upsert_record_ledger(
                    repository.db_path, rec_source_id, record, entry,
                    status="active", content_hash=file_hash,
                    last_modified_at=mtime, chunk_count=chunk_count,
                )
            report.new += 1
            _add_detail(report, {
                "source_id": rec_source_id,
                "source_path": record.source_path,
                "source_type": record.source_type,
                "action": "new",
            })
        return

    # Existing ledger record
    if existing.status == "active" and existing.content_hash == file_hash:
        # Unchanged
        report.unchanged += 1
        _add_detail(report, {
            "source_id": rec_source_id,
            "source_path": record.source_path,
            "source_type": record.source_type,
            "action": "unchanged",
        })
        return

    # Existing non-active records were never successfully indexed as an active
    # version. Once they become indexable, report them as new rather than
    # modified.
    action = "modified" if existing.status == "active" else "new"
    if action == "new":
        report.new += 1
    else:
        report.modified += 1

    # Changed content or retry from a non-active status.
    if not file_path.exists():
        if action == "new":
            report.new -= 1
        else:
            report.modified -= 1
        report.missing += 1
        _add_detail(report, {
            "source_id": rec_source_id,
            "source_path": record.source_path,
            "source_type": record.source_type,
            "action": "missing",
        })
        if not dry_run:
            _upsert_record_ledger(
                repository.db_path, rec_source_id, record, entry,
                status="missing", content_hash="",
                last_modified_at=mtime, chunk_count=0,
                error_message="source file no longer exists",
            )
    else:
        if not dry_run:
            try:
                chunk_count = _index_record(record, repository)
            except Exception as exc:
                if action == "new":
                    report.new -= 1
                else:
                    report.modified -= 1
                _record_index_error(
                    repository.db_path, rec_source_id, record, entry,
                    report, existing=existing, content_hash=file_hash,
                    last_modified_at=mtime, error=exc,
                )
                return
            report.indexed_records += 1
            _upsert_record_ledger(
                repository.db_path, rec_source_id, record, entry,
                status="active", content_hash=file_hash,
                last_modified_at=mtime, chunk_count=chunk_count,
            )
        _add_detail(report, {
            "source_id": rec_source_id,
            "source_path": record.source_path,
            "source_type": record.source_type,
            "action": action,
            "previous_hash": existing.content_hash,
            "new_hash": file_hash,
        })


def _record_index_error(
    db_path: Path,
    rec_source_id: str,
    record: IngestionRecord,
    entry: CatalogEntry,
    report: RefreshReport,
    *,
    existing: SourceLedgerRecord | None,
    content_hash: str,
    last_modified_at: str,
    error: Exception,
) -> None:
    """Record an index failure without replacing a previous active version."""
    message = str(error) or error.__class__.__name__
    report.errors += 1
    preserved_active = existing is not None and existing.status == "active"
    _add_detail(report, {
        "source_id": rec_source_id,
        "source_path": record.source_path,
        "source_type": record.source_type,
        "action": "error",
        "reason": message,
        "previous_status": existing.status if existing is not None else None,
        "preserved_previous_active": preserved_active,
    })
    if preserved_active:
        with connect(db_path) as conn:
            initialize_schema(conn)
            update_ledger_status(
                conn, rec_source_id, "error", error_message=message,
            )
        return
    _upsert_record_ledger(
        db_path, rec_source_id, record, entry,
        status="error", content_hash=content_hash,
        last_modified_at=last_modified_at, chunk_count=0,
        error_message=message,
    )


def _upsert_ledger_for_catalog_entry(
    db_path: Path,
    entry: CatalogEntry,
    *,
    status: str,
    error_message: str | None = None,
) -> None:
    """Write a catalog-level entry into the source ledger."""
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            entry.id,
            entry.path_or_alias,
            entry.source_type,
            project_name=entry.project_name,
            status=status,
            content_hash=None,
            last_modified_at=None,
            last_indexed_at=_now_iso() if status in ("active",) else None,
            error_message=error_message,
            chunk_count=0,
        )


def _qualified_source_path(record: IngestionRecord) -> str:
    """Return the path stored in source_ledger.source_path_or_alias.

    For agent_session exchanges this must match documents.path (which uses the
    ::exchange_N suffix) so that the JOIN in _iter_candidates finds rows.
    """
    exchange_index = record.metadata.get("exchange_index") if record.metadata else None
    if exchange_index is not None:
        return f"{record.source_path}::exchange_{exchange_index}"
    return record.source_path


def _upsert_record_ledger(
    db_path: Path,
    rec_source_id: str,
    record: IngestionRecord,
    entry: CatalogEntry,
    *,
    status: str,
    content_hash: str,
    last_modified_at: str,
    chunk_count: int,
    error_message: str | None = None,
) -> None:
    """Write a file-level entry into the source ledger."""
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            rec_source_id,
            _qualified_source_path(record),
            record.source_type,
            project_name=entry.project_name or record.project,
            status=status,
            content_hash=content_hash,
            last_modified_at=last_modified_at,
            last_indexed_at=_now_iso() if status == "active" else None,
            error_message=error_message,
            chunk_count=chunk_count,
        )


def _mark_missing_previous_records(
    entry: CatalogEntry,
    report: RefreshReport,
    db_path: Path,
    seen_source_ids: set[str],
    dry_run: bool,
    cache: _RefreshRunCache,
) -> None:
    """Mark active ledger records missing when a previously indexed file disappears."""
    parser_source_type = _parser_source_type_for_entry(entry)
    if parser_source_type is None or not db_path.exists():
        return

    for ledger_record in _active_ledger_records_for_entry(
        entry,
        parser_source_type,
        cache,
    ):
        if ledger_record.source_id in seen_source_ids:
            continue
        if Path(_physical_path(ledger_record.source_path_or_alias)).exists():
            continue

        report.missing += 1
        _add_detail(report, {
            "source_id": ledger_record.source_id,
            "source_path": ledger_record.source_path_or_alias,
            "source_type": ledger_record.source_type,
            "action": "missing",
            "reason": "previously indexed source file no longer exists",
        })
        if not dry_run:
            with connect(db_path) as conn:
                initialize_schema(conn)
                upsert_ledger_entry(
                    conn,
                    ledger_record.source_id,
                    ledger_record.source_path_or_alias,
                    ledger_record.source_type,
                    project_name=ledger_record.project_name,
                    parser_version=ledger_record.parser_version,
                    content_hash=ledger_record.content_hash,
                    last_modified_at=ledger_record.last_modified_at,
                    last_indexed_at=ledger_record.last_indexed_at,
                    status="missing",
                    error_message="previously indexed source file no longer exists",
                    chunk_count=ledger_record.chunk_count,
                )


def _active_ledger_records_for_entry(
    entry: CatalogEntry,
    parser_source_type: str,
    cache: _RefreshRunCache,
) -> list[SourceLedgerRecord]:
    """Return active ledger records physically under a catalog entry path."""
    grouped = cache.active_ledger_by_physical_path(parser_source_type)
    source_key = _normalized_cache_path_key(Path(entry.path_or_alias))

    if entry.source_type == "document":
        return grouped.get(source_key, [])

    if entry.source_type not in {"project_root", "agent_root"}:
        return []

    source_prefix = source_key.rstrip("\\/") + os.sep
    records: list[SourceLedgerRecord] = []
    for path_key, rows in grouped.items():
        if path_key == source_key or path_key.startswith(source_prefix):
            records.extend(rows)
    return records


def _physical_path(path_or_alias: str) -> str:
    """Strip ::exchange_N virtual suffix to get the real filesystem path."""
    sep = path_or_alias.find("::")
    return path_or_alias[:sep] if sep != -1 else path_or_alias


def _ledger_record_belongs_to_entry(record_path: str, entry: CatalogEntry) -> bool:
    record = Path(_physical_path(record_path)).resolve()
    source = Path(entry.path_or_alias).resolve()
    if entry.source_type == "document":
        return record == source
    if entry.source_type in {"project_root", "agent_root"}:
        try:
            record.relative_to(source)
            return True
        except ValueError:
            return False
    return False


# Text formatting

def format_refresh_report(report: RefreshReport) -> str:
    """Format a RefreshReport as a human-readable string."""
    summary = report.detail_summary()
    lines: list[str] = [
        "Refresh completed",
        "",
        f"  New: {report.new}",
        f"  Modified: {report.modified}",
        f"  Unchanged: {report.unchanged}",
        f"  Skipped: {report.skipped}",
        f"  Missing: {report.missing}",
        f"  Errors: {report.errors}",
        f"  Indexed records: {report.indexed_records}",
        f"  Catalog entries: {report.catalog_entries}",
        f"  Detail rows: {summary['total']}",
    ]
    by_action = summary.get("by_action", {})
    if by_action:
        lines.append(
            "  Detail by action: "
            + " ".join(f"{key}={value}" for key, value in by_action.items())
        )
    by_source_type = summary.get("by_source_type", {})
    if by_source_type:
        lines.append(
            "  Detail by source_type: "
            + " ".join(f"{key}={value}" for key, value in by_source_type.items())
        )
    top_reasons = summary.get("top_reasons", [])
    if top_reasons:
        reason_parts = [
            f"{item['count']}x {item['reason']}" for item in top_reasons[:5]
        ]
        lines.append("  Top reasons: " + "; ".join(reason_parts))
    if report.refresh_skipped:
        lines.append("  Refresh skipped: yes")
    if (
        report.auto_memory_candidates
        or report.auto_memory_created
        or report.auto_memory_duplicates
        or report.auto_memory_duplicate_active
        or report.auto_memory_duplicate_unverified
        or report.auto_memory_duplicate_rejected
        or report.auto_memory_low_confidence
        or report.auto_memory_limit_skipped
        or report.auto_memory_source_limit_skipped
        or report.auto_memory_non_document_skipped
        or report.auto_memory_noisy_session_skipped
    ):
        lines.extend([
            f"  Auto-memory candidates: {report.auto_memory_candidates}",
            f"  Auto-memory created: {report.auto_memory_created}",
            f"  Auto-memory duplicates skipped: {report.auto_memory_duplicates}",
            f"  Auto-memory active duplicates skipped: {report.auto_memory_duplicate_active}",
            f"  Auto-memory unverified duplicates skipped: {report.auto_memory_duplicate_unverified}",
            f"  Auto-memory rejected duplicates skipped: {report.auto_memory_duplicate_rejected}",
            f"  Auto-memory low-confidence skipped: {report.auto_memory_low_confidence}",
            f"  Auto-memory limit skipped: {report.auto_memory_limit_skipped}",
            f"  Auto-memory source-limit skipped: {report.auto_memory_source_limit_skipped}",
            f"  Auto-memory non-document skipped: {report.auto_memory_non_document_skipped}",
            f"  Auto-memory noisy-session skipped: {report.auto_memory_noisy_session_skipped}",
        ])
    return "\n".join(lines)

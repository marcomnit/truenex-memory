"""Ingestion engine: load manifest, parse sources, index into repository."""

from __future__ import annotations

from pathlib import Path
import json
import tempfile

from truenex_memory.core.chunker import chunk_text
from truenex_memory.ingestion.manifest import (
    PARSE_LATER_SOURCE_TYPES,
    IngestionRecord,
    SourceManifest,
)
from truenex_memory.ingestion.parsers import get_parser
from truenex_memory.store.repository import MemoryRepository


def ingest_manifest(
    manifest_path: Path,
    project_root: Path,
    repository: MemoryRepository,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Run ingestion from a source manifest.

    Args:
        manifest_path: Path to the manifest JSON file.
        project_root: Root directory for resolving relative source paths.
        repository: MemoryRepository for upserting documents.
        dry_run: If True, report only without modifying the database.

    Returns:
        Report dict with keys: index_now, parse_later, skipped, errors.
    """
    report: dict[str, list[dict[str, object]]] = {
        "index_now": [],
        "parse_later": [],
        "skipped": [],
        "errors": [],
    }

    manifest_dir = manifest_path.parent.resolve()
    root = project_root.resolve()

    try:
        manifest = SourceManifest.from_path(manifest_path)
    except (FileNotFoundError, ValueError) as exc:
        report["errors"].append({"source_path": str(manifest_path), "error": str(exc)})
        return report

    for entry in manifest.sources:
        if entry.source_type in PARSE_LATER_SOURCE_TYPES:
            report["parse_later"].append(
                {
                    "source_type": entry.source_type,
                    "source_path": entry.source_path,
                    "source_tool": entry.source_tool,
                    "reason": "parser not yet implemented",
                }
            )
            continue

        parser = get_parser(entry.source_type)
        if parser is None:
            report["skipped"].append(
                {
                    "source_type": entry.source_type,
                    "source_path": entry.source_path,
                    "source_tool": entry.source_tool,
                    "reason": f"no parser registered for {entry.source_type!r}",
                }
            )
            continue

        source_dir = _resolve_source_dir(entry.source_path, manifest_dir, root)

        try:
            records = parser(
                source_dir,
                manifest.project,
                entry.source_tool,
                entry.privacy_scope,
            )
        except Exception as exc:
            report["errors"].append(
                {
                    "source_type": entry.source_type,
                    "source_path": entry.source_path,
                    "source_tool": entry.source_tool,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        for record in records:
            report_item = _record_report_item(record)
            report["index_now"].append(report_item)
            if not dry_run:
                try:
                    _index_record(record, root, repository)
                except Exception as exc:
                    report["errors"].append(
                        {
                            "source_type": record.source_type,
                            "source_path": record.source_path,
                            "source_tool": record.source_tool,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    report["index_now"].remove(report_item)

    return report


def _resolve_source_dir(source_path: str, manifest_dir: Path, project_root: Path) -> Path:
    """Resolve a source path from the manifest.

    Relative paths are resolved against the manifest directory first,
    then the project root. Absolute paths are used as-is.
    """
    candidate = Path(source_path)
    if candidate.is_absolute():
        return candidate

    # Try manifest-relative first
    manifest_relative = (manifest_dir / candidate).resolve()
    if manifest_relative.exists():
        return manifest_relative

    # Fall back to project-root-relative
    project_relative = (project_root / candidate).resolve()
    return project_relative


def _record_report_item(record: IngestionRecord) -> dict[str, object]:
    return {
        "project": record.project,
        "source_type": record.source_type,
        "source_path": record.source_path,
        "source_tool": record.source_tool,
        "privacy_scope": record.privacy_scope,
        "chars": len(record.text),
        "session_id": record.session_id,
    }


def _index_record(record: IngestionRecord, project_root: Path, repository: MemoryRepository) -> None:
    """Index a single ingestion record into the repository.

    Writes text to a temporary file so it can flow through the standard
    upsert_document path. The source_path stored in the DB is the logical
    path from the ingestion record.
    """
    indexed_text = _record_text(record)
    chunks = chunk_text(indexed_text)
    if not chunks:
        return

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        encoding="utf-8",
        delete=False,
    ) as tmp:
        tmp.write(indexed_text)
        tmp_path = Path(tmp.name)

    try:
        repository.upsert_document(
            path=tmp_path,
            relative_path=record.source_path,
            chunks=chunks,
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _record_text(record: IngestionRecord) -> str:
    """Return index text with a small metadata preamble.

    SQLite currently stores document path and chunk text but not arbitrary
    ingestion metadata. Keeping a compact preamble makes project/source/session
    fields searchable without changing the schema yet.
    """
    metadata = {
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
    metadata = {key: value for key, value in metadata.items() if value not in (None, "")}
    preamble = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return f"TRUENEX_INGESTION_METADATA {preamble}\n\n{record.text}"

"""Tests for source catalog/ledger health cleanup."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from typer.testing import CliRunner

from truenex_memory.cli.main import app
from truenex_memory.discovery.source_catalog import CatalogEntry, SourceCatalog, source_id
from truenex_memory.ingestion.global_auto_status import build_auto_status
from truenex_memory.ingestion.global_refresh import refresh
from truenex_memory.ingestion.global_source_health import build_source_health
from truenex_memory.store.source_ledger import get_ledger_entry, upsert_ledger_entry
from truenex_memory.store.sqlite import connect, initialize_schema


runner = CliRunner()


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"task_work_source_health_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _make_catalog(path: Path, entries: list[CatalogEntry]) -> None:
    SourceCatalog(entries=entries).save(path)


def _project_entry(project_dir: Path) -> CatalogEntry:
    return CatalogEntry(
        id=source_id("project_root", str(project_dir)),
        source_type="project_root",
        path_or_alias=str(project_dir),
        project_name="project",
        discovered_from=["codex-sessions"],
    )


def _document_entry(document_path: Path) -> CatalogEntry:
    return CatalogEntry(
        id=source_id("document", str(document_path)),
        source_type="document",
        path_or_alias=str(document_path),
        project_name="project",
        discovered_from=["codex-sessions"],
    )


def test_sources_health_dry_run_does_not_disable_catalog_or_update_ledger() -> None:
    wd = _workdir("dry_run")
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    missing_doc = wd / "missing.md"
    entry = _document_entry(missing_doc)
    _make_catalog(catalog_path, [entry])
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            entry.id,
            str(missing_doc),
            "project_docs",
            status="missing",
            error_message="source path not found",
        )

    report = build_source_health(catalog_path, db_path, apply=False)

    assert report.dry_run is True
    assert report.missing_catalog_entries == 1
    assert report.cleanup_candidates == 2
    reloaded = SourceCatalog.load(catalog_path)
    assert reloaded.entries[0].confirmation_status == "confirmed"
    with connect(db_path) as conn:
        ledger = get_ledger_entry(conn, entry.id)
    assert ledger is not None
    assert ledger.status == "missing"


def test_sources_cleanup_disables_missing_catalog_and_marks_ledger_expected() -> None:
    wd = _workdir("apply")
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    missing_doc = wd / "missing.md"
    entry = _document_entry(missing_doc)
    _make_catalog(catalog_path, [entry])
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            entry.id,
            str(missing_doc),
            "project_docs",
            status="missing",
            error_message="source path not found",
        )

    report = build_source_health(catalog_path, db_path, apply=True)

    assert report.dry_run is False
    assert report.catalog_changed == 1
    assert report.ledger_changed == 1
    reloaded = SourceCatalog.load(catalog_path)
    assert reloaded.entries[0].confirmation_status == "disabled"
    with connect(db_path) as conn:
        ledger = get_ledger_entry(conn, entry.id)
    assert ledger is not None
    assert ledger.status == "skipped"
    assert ledger.error_message == "disabled catalog source: local path not indexed"


def test_sources_cleanup_disables_relative_catalog_path_even_if_cwd_has_file() -> None:
    wd = _workdir("relative")
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    entry = CatalogEntry(
        id=source_id("document", "README.md"),
        source_type="document",
        path_or_alias="README.md",
        project_name="project",
        discovered_from=["codex-sessions"],
    )
    _make_catalog(catalog_path, [entry])
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            entry.id,
            "README.md",
            "document",
            status="missing",
            error_message="source path not found",
        )

    report = build_source_health(catalog_path, db_path, apply=True)

    assert report.catalog_changed == 1
    reloaded = SourceCatalog.load(catalog_path)
    assert reloaded.entries[0].confirmation_status == "disabled"
    with connect(db_path) as conn:
        ledger = get_ledger_entry(conn, entry.id)
    assert ledger is not None
    assert ledger.status == "skipped"
    assert ledger.error_message == "disabled catalog source: local path not indexed"


def test_sources_cleanup_marks_removed_indexed_file_as_expected_skip() -> None:
    wd = _workdir("removed")
    project_dir = wd / "project"
    project_dir.mkdir()
    readme = project_dir / "README.md"
    removed = project_dir / "removed.md"
    readme.write_text("# Project\n", encoding="utf-8")
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            "project_docs:removed",
            str(removed),
            "project_docs",
            status="missing",
            error_message="previously indexed source file no longer exists",
        )

    report = build_source_health(catalog_path, db_path, apply=True)

    assert report.ledger_changed == 1
    with connect(db_path) as conn:
        ledger = get_ledger_entry(conn, "project_docs:removed")
    assert ledger is not None
    assert ledger.status == "skipped"
    assert ledger.error_message == "removed local source: no active local content"


def test_no_indexable_records_refresh_clears_prior_catalog_error() -> None:
    wd = _workdir("no_records")
    session_file = wd / "history.jsonl"
    session_file.write_text(
        json.dumps({"timestamp": 1_767_225_600, "display": "no message objects"}) + "\n",
        encoding="utf-8",
    )
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    entry = CatalogEntry(
        id=source_id("agent_root", str(session_file)),
        source_type="agent_root",
        path_or_alias=str(session_file),
        discovered_from=["claude-history"],
    )
    _make_catalog(catalog_path, [entry])
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            entry.id,
            str(session_file),
            "agent_root",
            status="error",
            error_message="OSError: [Errno 22] Invalid argument",
        )

    report = refresh(catalog_path, db_path, stability_seconds=0)

    assert report.skipped == 1
    with connect(db_path) as conn:
        ledger = get_ledger_entry(conn, entry.id)
    assert ledger is not None
    assert ledger.status == "skipped"
    assert ledger.error_message == "no indexable records"
    status = build_auto_status(catalog_path, db_path)
    assert status.ready is False  # no active indexed sources yet
    assert status.expected_skipped_sources == 1
    assert status.skipped_sources == 0


def test_sources_health_cli_json_and_cleanup_dry_run() -> None:
    wd = _workdir("cli")
    home = wd / "home"
    tm_dir = home / ".truenex-memory"
    tm_dir.mkdir(parents=True)
    catalog_path = tm_dir / "sources.json"
    db_path = tm_dir / "truenex_memory.db"
    entry = _document_entry(wd / "missing.md")
    _make_catalog(catalog_path, [entry])

    result = runner.invoke(app, ["global", "sources", "health", "--home", str(home), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["missing_catalog_entries"] == 1

    cleanup = runner.invoke(app, ["global", "sources", "cleanup", "--home", str(home)])
    assert cleanup.exit_code == 0
    assert "dry-run" in cleanup.stdout.lower()
    assert SourceCatalog.load(catalog_path).entries[0].confirmation_status == "confirmed"

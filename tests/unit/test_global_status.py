"""Unit tests for global status reporting."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from typer.testing import CliRunner

from truenex_memory.cli.main import app
from truenex_memory.discovery.source_catalog import CatalogEntry, SourceCatalog, source_id
from truenex_memory.ingestion.global_refresh import refresh
from truenex_memory.ingestion.global_status import (
    build_global_status,
    format_status_report,
)
from truenex_memory.store.source_ledger import upsert_ledger_entry
from truenex_memory.store.sqlite import connect, initialize_schema


runner = CliRunner()


def _workdir(name: str) -> Path:
    import shutil

    path = (
        Path(__file__).resolve().parents[1]
        / "unit"
        / f"task_work_status_{name}_{uuid.uuid4().hex}"
    )
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _write_catalog(path: Path, entries: list[CatalogEntry]) -> None:
    SourceCatalog(entries=entries).save(path)


def _project_entry(project_dir: Path) -> CatalogEntry:
    return CatalogEntry(
        id=source_id("project_root", str(project_dir)),
        source_type="project_root",
        path_or_alias=str(project_dir),
        project_name="project",
        discovered_from=["codex-sessions"],
    )


def test_status_missing_catalog_and_db_is_read_only() -> None:
    wd = _workdir("missing")
    catalog_path = wd / ".truenex-memory" / "sources.json"
    db_path = wd / ".truenex-memory" / "truenex_memory.db"

    report = build_global_status(catalog_path, db_path)

    assert report.catalog_exists is False
    assert report.db_exists is False
    assert "Catalog not found" in "\n".join(report.warnings)
    assert "Database not found" in "\n".join(report.warnings)
    assert not catalog_path.exists()
    assert not db_path.exists()
    assert not catalog_path.parent.exists()


def test_status_empty_catalog_without_db() -> None:
    wd = _workdir("empty_catalog")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    _write_catalog(catalog_path, [])

    report = build_global_status(catalog_path, db_path)

    assert report.catalog_exists is True
    assert report.catalog_version == "1"
    assert report.catalog_total_entries == 0
    assert report.catalog_confirmed_entries == 0
    assert report.db_exists is False


def test_status_counts_catalog_entries() -> None:
    wd = _workdir("catalog_counts")
    project_dir = wd / "project"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    entries = [
        _project_entry(project_dir),
        CatalogEntry(
            id=source_id("server_alias", "example-core"),
            source_type="server_alias",
            path_or_alias="example-core",
            discovered_from=["codex-sessions"],
        ),
        CatalogEntry(
            id=source_id("document", str(wd / "notes.md")),
            source_type="document",
            path_or_alias=str(wd / "notes.md"),
            confirmation_status="candidate",
        ),
    ]
    _write_catalog(catalog_path, entries)

    report = build_global_status(catalog_path, db_path)

    assert report.catalog_total_entries == 3
    assert report.catalog_confirmed_entries == 2
    assert report.catalog_by_source_type == {
        "document": 1,
        "project_root": 1,
        "server_alias": 1,
    }
    assert report.catalog_by_confirmation_status == {"candidate": 1, "confirmed": 2}


def test_status_reports_refresh_db_ledger_and_indexed_counts() -> None:
    wd = _workdir("populated")
    project_dir = wd / "project"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("# Hello\n\nWorld.\n", encoding="utf-8")
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _write_catalog(catalog_path, [_project_entry(project_dir)])

    refresh(catalog_path, db_path)
    report = build_global_status(catalog_path, db_path)

    assert report.db_exists is True
    assert report.ledger_total_rows >= 1
    assert report.ledger_by_status["active"] >= 1
    assert report.ledger_by_source_type["project_docs"] >= 1
    assert report.indexed_documents >= 1
    assert report.indexed_chunks >= 1
    assert report.last_indexed_at is not None
    assert report.problem_counts == {"missing": 0, "error": 0, "skipped": 0}


def test_status_reports_recent_problem_entries() -> None:
    wd = _workdir("problems")
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _write_catalog(catalog_path, [])
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            "missing:1",
            str(wd / "missing.md"),
            "project_docs",
            status="missing",
            error_message="gone",
        )
        upsert_ledger_entry(
            conn,
            "error:1",
            str(wd / "bad.md"),
            "project_docs",
            status="error",
            error_message="parse failed",
        )

    report = build_global_status(catalog_path, db_path)

    assert report.problem_counts["missing"] == 1
    assert report.problem_counts["error"] == 1
    assert len(report.recent_problems) == 2
    assert {item["status"] for item in report.recent_problems} == {"missing", "error"}


def test_status_invalid_catalog_json_reports_warning() -> None:
    wd = _workdir("invalid_catalog")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    catalog_path.write_text("{not json", encoding="utf-8")

    report = build_global_status(catalog_path, db_path)

    assert report.catalog_exists is True
    assert report.catalog_version is None
    assert "invalid/unreadable" in "\n".join(report.warnings)


def test_status_non_object_catalog_json_reports_warning() -> None:
    wd = _workdir("non_object_catalog")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    catalog_path.write_text(json.dumps(["not", "object"]), encoding="utf-8")

    report = build_global_status(catalog_path, db_path)

    assert report.catalog_exists is True
    assert report.catalog_version is None
    assert "Catalog must be a JSON object" in "\n".join(report.warnings)


def test_status_malformed_catalog_entries_do_not_crash() -> None:
    wd = _workdir("malformed_catalog")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    catalog_path.write_text(
        json.dumps({"version": "1", "entries": ["bad", {"source_type": "document"}]}),
        encoding="utf-8",
    )

    report = build_global_status(catalog_path, db_path)

    assert report.catalog_total_entries == 2
    assert report.catalog_by_source_type["document"] == 1
    assert "non-object entries" in "\n".join(report.warnings)


def test_status_db_without_expected_tables_reports_warnings() -> None:
    wd = _workdir("legacy_db")
    catalog_path = wd / "sources.json"
    db_path = wd / "legacy.db"
    _write_catalog(catalog_path, [])
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE legacy_data (value TEXT)")
        conn.commit()

    report = build_global_status(catalog_path, db_path)

    warnings = "\n".join(report.warnings)
    assert report.db_exists is True
    assert "source_ledger table not found" in warnings
    assert "documents table not found" in warnings
    assert "chunks table not found" in warnings


def test_status_non_sqlite_db_reports_warning() -> None:
    wd = _workdir("not_sqlite")
    catalog_path = wd / "sources.json"
    db_path = wd / "not-sqlite.db"
    _write_catalog(catalog_path, [])
    db_path.write_text("not a sqlite database", encoding="utf-8")

    report = build_global_status(catalog_path, db_path)

    assert report.db_exists is True
    assert "Database exists but cannot be read" in "\n".join(report.warnings)


def test_format_status_report_includes_key_sections() -> None:
    wd = _workdir("format")
    report = build_global_status(wd / "missing.json", wd / "missing.db")

    text = format_status_report(report)

    assert "Global Status" in text
    assert "Catalog:" in text
    assert "Database:" in text
    assert "not found" in text


def test_cli_global_status_text_does_not_create_default_paths() -> None:
    wd = _workdir("cli_text")
    home = wd / "home"

    result = runner.invoke(app, ["global", "status", "--home", str(home)])

    assert result.exit_code == 0
    assert "Global Status" in result.stdout
    assert not (home / ".truenex-memory").exists()


def test_cli_global_status_json() -> None:
    wd = _workdir("cli_json")
    home = wd / "home"

    result = runner.invoke(app, ["global", "status", "--home", str(home), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["catalog"]["exists"] is False
    assert payload["database"]["exists"] is False
    assert payload["warnings"]


def test_cli_global_status_custom_catalog_and_db() -> None:
    wd = _workdir("cli_custom")
    project_dir = wd / "project"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("# Hello\n", encoding="utf-8")
    catalog_path = wd / "sources.json"
    db_path = wd / "memory.db"
    _write_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    result = runner.invoke(
        app,
        [
            "global",
            "status",
            "--catalog",
            str(catalog_path),
            "--db",
            str(db_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["catalog"]["confirmed_entries"] == 1
    assert payload["database"]["exists"] is True
    assert payload["ledger"]["total_rows"] >= 1

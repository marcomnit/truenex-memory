"""Unit tests for global context command."""

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
from truenex_memory.ingestion.global_context import (
    ProjectContextReport,
    _chunk_row_to_dict,
    _doc_ids_from_ledger,
    _strip_ingestion_metadata,
    build_project_context,
    format_context_report,
)
from truenex_memory.ingestion.global_refresh import refresh
from truenex_memory.store.source_ledger import upsert_ledger_entry
from truenex_memory.store.sqlite import connect, initialize_schema


runner = CliRunner()


def _workdir(name: str) -> Path:
    import shutil

    path = (
        Path(__file__).resolve().parents[1]
        / "unit"
        / f"task_work_context_{name}_{uuid.uuid4().hex}"
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


def _project_entry(project_dir: Path, *, project_name: str = "myproject") -> CatalogEntry:
    return CatalogEntry(
        id=source_id("project_root", str(project_dir)),
        source_type="project_root",
        path_or_alias=str(project_dir),
        project_name=project_name,
        discovered_from=["codex-sessions"],
    )


# ── Test 1: missing catalog/db is read-only and warns ─────────────────

def test_context_missing_catalog_and_db_is_read_only() -> None:
    wd = _workdir("missing")
    catalog_path = wd / ".truenex-memory" / "sources.json"
    db_path = wd / ".truenex-memory" / "truenex_memory.db"

    report = build_project_context("anything", catalog_path, db_path)

    assert report.resolved is False
    assert "Catalog not found" in "\n".join(report.warnings)
    assert not catalog_path.exists()
    assert not db_path.exists()
    assert not catalog_path.parent.exists()


# ── Test 2: exact project_name match returns one project root ──────────

def test_context_exact_project_name_match() -> None:
    wd = _workdir("exact_name")
    project_dir = wd / "myproject"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    _write_catalog(catalog_path, [
        _project_entry(project_dir, project_name="myproject"),
        _project_entry(wd / "other", project_name="otherproject"),
    ])

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is True
    assert report.resolution_method == "exact_name"
    assert len(report.catalog_roots) == 1
    assert report.catalog_roots[0]["project_name"] == "myproject"
    assert report.catalog_roots[0]["source_type"] == "project_root"


# ── Test 2b: case-insensitive project_name match ─────────────────────

def test_context_case_insensitive_project_name_match() -> None:
    wd = _workdir("case_insensitive")
    project_dir = wd / "MyProject"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    _write_catalog(catalog_path, [
        _project_entry(project_dir, project_name="MyProject"),
    ])

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is True
    assert report.resolution_method == "exact_name"
    assert report.catalog_roots[0]["project_name"] == "MyProject"


# ── Test 3: basename/path match works when project_name is absent ─────

def test_context_basename_match_when_project_name_absent() -> None:
    wd = _workdir("basename")
    project_dir = wd / "myproject"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    entry = CatalogEntry(
        id=source_id("project_root", str(project_dir)),
        source_type="project_root",
        path_or_alias=str(project_dir),
        project_name=None,
        discovered_from=["codex-sessions"],
    )
    _write_catalog(catalog_path, [entry])

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is True
    assert report.resolution_method == "basename"
    assert len(report.catalog_roots) == 1
    assert report.catalog_roots[0]["project_name"] is None


def test_context_exact_path_alias_match() -> None:
    wd = _workdir("path_alias")
    project_dir = wd / "myproject"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    entry = CatalogEntry(
        id=source_id("project_root", str(project_dir)),
        source_type="project_root",
        path_or_alias=str(project_dir),
        project_name=None,
        discovered_from=["codex-sessions"],
    )
    _write_catalog(catalog_path, [entry])

    report = build_project_context(str(project_dir), catalog_path, db_path)

    assert report.resolved is True
    assert report.resolution_method == "path_alias"
    assert report.catalog_roots[0]["path_or_alias"] == str(project_dir)


def test_context_related_doc_does_not_match_path_prefix_sibling() -> None:
    wd = _workdir("path_sibling")
    project_dir = wd / "project"
    sibling_doc = str(wd / "project-other" / "notes.md")
    inside_doc = str(project_dir / "docs" / "notes.md")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    entries = [
        _project_entry(project_dir, project_name="project"),
        CatalogEntry(
            id=source_id("document", inside_doc),
            source_type="document",
            path_or_alias=inside_doc,
            discovered_from=["codex-sessions"],
        ),
        CatalogEntry(
            id=source_id("document", sibling_doc),
            source_type="document",
            path_or_alias=sibling_doc,
            discovered_from=["codex-sessions"],
        ),
    ]
    _write_catalog(catalog_path, entries)

    report = build_project_context("project", catalog_path, db_path)

    assert report.resolved is True
    assert [doc["path_or_alias"] for doc in report.catalog_documents] == [inside_doc]


# ── Test 4: ambiguous substring match reports ambiguity ───────────────

def test_context_ambiguous_substring_match() -> None:
    wd = _workdir("ambiguous")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    entries = [
        CatalogEntry(
            id=source_id("project_root", str(wd / "myproject-frontend")),
            source_type="project_root",
            path_or_alias=str(wd / "myproject-frontend"),
            project_name="myproject-frontend",
            discovered_from=["codex-sessions"],
        ),
        CatalogEntry(
            id=source_id("project_root", str(wd / "myproject-backend")),
            source_type="project_root",
            path_or_alias=str(wd / "myproject-backend"),
            project_name="myproject-backend",
            discovered_from=["codex-sessions"],
        ),
    ]
    _write_catalog(catalog_path, entries)

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is False
    assert len(report.ambiguous_candidates) == 2
    assert "Ambiguous" in "\n".join(report.warnings)


def test_context_ambiguous_exact_name_matches() -> None:
    """Two projects with identical project_name should be ambiguous."""
    wd = _workdir("ambiguous_name")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    entries = [
        CatalogEntry(
            id=source_id("project_root", str(wd / "path-a")),
            source_type="project_root",
            path_or_alias=str(wd / "path-a"),
            project_name="duplicate",
            discovered_from=["codex-sessions"],
        ),
        CatalogEntry(
            id=source_id("project_root", str(wd / "path-b")),
            source_type="project_root",
            path_or_alias=str(wd / "path-b"),
            project_name="duplicate",
            discovered_from=["codex-sessions"],
        ),
    ]
    _write_catalog(catalog_path, entries)

    report = build_project_context("duplicate", catalog_path, db_path)

    assert report.resolved is False
    assert len(report.ambiguous_candidates) == 2
    assert "Ambiguous" in "\n".join(report.warnings)


# ── Test 4b: project not found ────────────────────────────────────────

def test_context_project_not_found() -> None:
    wd = _workdir("not_found")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    _write_catalog(catalog_path, [_project_entry(wd / "other")])

    report = build_project_context("nonexistent", catalog_path, db_path)

    assert report.resolved is False
    assert any("not found" in w.lower() for w in report.warnings)
    assert not report.ambiguous_candidates


# ── Test 5: after refresh, context reports active ledger rows ─────────

def test_context_after_refresh_reports_ledger_and_indexed() -> None:
    wd = _workdir("after_refresh")
    project_dir = wd / "myproject"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("# Hello\n\nWorld.\n", encoding="utf-8")
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _write_catalog(catalog_path, [_project_entry(project_dir, project_name="myproject")])

    refresh(catalog_path, db_path)

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is True
    assert report.resolution_method == "exact_name"
    assert len(report.ledger_entries) >= 1
    assert any(le["status"] == "active" for le in report.ledger_entries)
    assert len(report.indexed_documents) >= 1
    assert len(report.indexed_chunks) >= 1


# ── Test 6: CLI text and JSON work with custom catalog/db/home ────────

def test_cli_context_text_output() -> None:
    wd = _workdir("cli_text")
    project_dir = wd / "myproject"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    _write_catalog(catalog_path, [_project_entry(project_dir, project_name="myproject")])

    result = runner.invoke(
        app,
        [
            "global", "context", "myproject",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ],
    )

    assert result.exit_code == 0
    assert "Global Context: myproject" in result.stdout
    assert "exact_name" in result.stdout
    assert str(project_dir) in result.stdout


def test_cli_context_json_output() -> None:
    wd = _workdir("cli_json")
    project_dir = wd / "myproject"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    _write_catalog(catalog_path, [_project_entry(project_dir, project_name="myproject")])

    result = runner.invoke(
        app,
        [
            "global", "context", "myproject",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["resolved"] is True
    assert payload["resolution_method"] == "exact_name"
    assert len(payload["catalog"]["roots"]) == 1
    assert payload["catalog"]["roots"][0]["project_name"] == "myproject"


def test_cli_context_with_home() -> None:
    wd = _workdir("cli_home")
    home = wd / "home"
    home.mkdir()
    catalog_dir = home / ".truenex-memory"
    catalog_dir.mkdir(parents=True)
    project_dir = wd / "myproject"
    catalog_path = catalog_dir / "sources.json"
    _write_catalog(catalog_path, [_project_entry(project_dir, project_name="myproject")])

    result = runner.invoke(
        app,
        ["global", "context", "myproject", "--home", str(home)],
    )

    assert result.exit_code == 0
    assert "Global Context: myproject" in result.stdout


def test_cli_context_json_with_limit() -> None:
    wd = _workdir("cli_limit")
    project_dir = wd / "myproject"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("# Hello\n\nWorld.\n", encoding="utf-8")
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _write_catalog(catalog_path, [_project_entry(project_dir, project_name="myproject")])

    refresh(catalog_path, db_path)

    result = runner.invoke(
        app,
        [
            "global", "context", "myproject",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
            "--limit", "3",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["resolved"] is True
    # limit applies to excerpted docs/chunks
    assert len(payload["indexed"]["documents"]) <= 3
    assert len(payload["indexed"]["chunks"]) <= 3


# ── Test 7: server_alias is reported only as hint ─────────────────────

def test_context_server_alias_reported_as_hint_only() -> None:
    wd = _workdir("server_hint")
    project_dir = wd / "myproject"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    entries = [
        _project_entry(project_dir, project_name="myproject"),
        CatalogEntry(
            id=source_id("server_alias", "example-core"),
            source_type="server_alias",
            path_or_alias="example-core",
            discovered_from=["myproject"],
        ),
    ]
    _write_catalog(catalog_path, entries)

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is True
    assert len(report.catalog_server_aliases) >= 1
    alias = report.catalog_server_aliases[0]
    assert alias["source_type"] == "server_alias"
    # Text output must state it is a hint
    text = format_context_report(report)
    assert "hints" in text.lower()
    assert "not executed" in text.lower()
    assert report.db_path == str(db_path)  # no DB operations executed


# ── Test 8: invalid catalog reports warning ──────────────────────────

def test_context_invalid_catalog_json_reports_warning() -> None:
    wd = _workdir("invalid_catalog")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    catalog_path.write_text("{not json", encoding="utf-8")

    report = build_project_context("anything", catalog_path, db_path)

    assert report.resolved is False
    assert "invalid/unreadable" in "\n".join(report.warnings)


# ── Test 9: only confirmed entries are matched ────────────────────────

def test_context_only_confirmed_entries_are_matched() -> None:
    wd = _workdir("only_confirmed")
    project_dir = wd / "myproject"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    entries = [
        CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            confirmation_status="candidate",  # NOT confirmed
            discovered_from=["codex-sessions"],
        ),
    ]
    _write_catalog(catalog_path, entries)

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is False
    assert "no confirmed entries" in "\n".join(report.warnings).lower()


# ── Test 10: substring fallback match ─────────────────────────────────

def test_context_substring_fallback_match() -> None:
    wd = _workdir("substring")
    project_dir = wd / "long-project-name-2024"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    _write_catalog(catalog_path, [
        _project_entry(project_dir, project_name="long-project-name-2024"),
    ])

    report = build_project_context("long", catalog_path, db_path)

    assert report.resolved is True
    assert report.resolution_method == "substring"
    assert len(report.catalog_roots) == 1


# ── Test 11: format_context_report includes key sections ──────────────

def test_format_context_report_includes_key_sections() -> None:
    wd = _workdir("format")
    report = build_project_context("nonexistent", wd / "missing.json", wd / "missing.db")

    text = format_context_report(report)

    assert "Global Context:" in text
    assert "WARNING" in text
    assert "Catalog:" in text
    assert "Database:" in text


# ── Test 12: DB without expected tables does not crash ────────────────

def test_context_db_without_expected_tables() -> None:
    wd = _workdir("legacy_db")
    project_dir = wd / "myproject"
    catalog_path = wd / "sources.json"
    db_path = wd / "legacy.db"
    _write_catalog(catalog_path, [_project_entry(project_dir, project_name="myproject")])
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE legacy_data (value TEXT)")
        conn.commit()

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is True
    warnings = "\n".join(report.warnings)
    assert "source_ledger table not found" in warnings
    assert "documents table not found" in warnings


# ── Test 13: json output is stable and testable ───────────────────────

def test_context_to_dict_contains_expected_keys() -> None:
    wd = _workdir("dict_keys")
    project_dir = wd / "myproject"
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    _write_catalog(catalog_path, [_project_entry(project_dir, project_name="myproject")])

    report = build_project_context("myproject", catalog_path, db_path)
    d = report.to_dict()

    expected_keys = {
        "project_query", "catalog_path", "db_path",
        "resolved", "resolution_method", "resolution_notes",
        "catalog", "ledger", "indexed",
        "memory_nodes", "ambiguous_candidates", "warnings",
    }
    assert set(d.keys()) == expected_keys
    assert set(d["catalog"].keys()) == {"roots", "documents", "server_aliases"}
    assert set(d["indexed"].keys()) == {"documents", "chunks"}
    assert isinstance(d["resolved"], bool)
    assert isinstance(d["warnings"], list)
    assert isinstance(d["memory_nodes"], list)


# ── Test 14: related documents and server_aliases are discovered ─────

def test_context_discovers_related_docs_and_servers() -> None:
    wd = _workdir("related")
    project_dir = wd / "myproject"
    doc_path = str(project_dir / "docs" / "notes.md")
    catalog_path = wd / "sources.json"
    db_path = wd / "missing.db"
    entries = [
        _project_entry(project_dir, project_name="myproject"),
        CatalogEntry(
            id=source_id("document", doc_path),
            source_type="document",
            path_or_alias=doc_path,
            discovered_from=["codex-sessions"],
        ),
        CatalogEntry(
            id=source_id("document", str(wd / "unrelated" / "notes.md")),
            source_type="document",
            path_or_alias=str(wd / "unrelated" / "notes.md"),
            discovered_from=["codex-sessions"],
        ),
        CatalogEntry(
            id=source_id("server_alias", "myproject-api"),
            source_type="server_alias",
            path_or_alias="myproject-api",
            discovered_from=["myproject-sessions"],
        ),
    ]
    _write_catalog(catalog_path, entries)

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is True
    assert len(report.catalog_documents) == 1
    assert report.catalog_documents[0]["path_or_alias"] == doc_path
    assert len(report.catalog_server_aliases) >= 1


# ── Test 15: ledger JOIN finds session documents ──────────────────────

def test_ledger_join_finds_session_documents() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            path TEXT,
            filename TEXT,
            content_hash TEXT,
            last_indexed_at TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE source_ledger (
            source_id TEXT,
            source_path_or_alias TEXT,
            project_name TEXT,
            status TEXT,
            source_type TEXT,
            parser_version TEXT,
            content_hash TEXT,
            last_modified_at TEXT,
            last_indexed_at TEXT,
            error_message TEXT,
            chunk_count INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
        INSERT INTO documents VALUES (
            'doc-1', NULL, 'session.jsonl::exchange_0', 'session.jsonl',
            'abc123', '2026-01-01', '2026-01-01', '2026-01-01'
        );
        INSERT INTO source_ledger VALUES (
            'root-1', 'session.jsonl::exchange_0', 'myproject', 'active',
            'exchange', '1', 'abc123', '2026-01-01', '2026-01-01',
            NULL, 1, '2026-01-01', '2026-01-01'
        );
    """)

    result = _doc_ids_from_ledger(conn, {"myproject"}, {"root-1"})
    conn.close()

    assert "doc-1" in result


# ── Test 16: chunk_row strips preamble before truncation ─────────────

def test_chunk_row_strips_preamble() -> None:
    preamble = 'TRUENEX_INGESTION_METADATA {"created_at": "2026-05-02T07:58:38"}'
    actual_text = "Testo reale del documento."
    raw = f"{preamble}\n\n{actual_text}"

    assert _strip_ingestion_metadata(raw) == actual_text

    # Simulate a sqlite3.Row via a namedtuple-style dict row through a real connection
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE chunks (
            id TEXT, document_id TEXT, chunk_index INTEGER, heading_path TEXT,
            content TEXT, content_hash TEXT, token_count INTEGER, created_at TEXT
        );
    """)
    conn.execute(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("c1", "d1", 0, None, raw, "h", 10, "2026-01-01"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM chunks WHERE id = 'c1'").fetchone()
    result = _chunk_row_to_dict(row, limit_chars=400)
    conn.close()

    assert result["content_excerpt"].startswith("Testo reale")
    assert "TRUENEX_INGESTION_METADATA" not in result["content_excerpt"]


# ── Test 17: memory_nodes included in report ─────────────────────────

def test_memory_nodes_included_in_report() -> None:
    wd = _workdir("memory_nodes")
    project_dir = wd / "myproject"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("# Hello\n\nWorld.\n", encoding="utf-8")
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _write_catalog(catalog_path, [_project_entry(project_dir, project_name="myproject")])

    # Bootstrap schema and insert a memory_node manually
    conn_rw = sqlite3.connect(str(db_path))
    conn_rw.row_factory = sqlite3.Row
    initialize_schema(conn_rw)
    # Insert a memory node (project_id='default', active, confidence >= 0.5)
    conn_rw.execute("""
        INSERT OR IGNORE INTO memory_nodes
            (id, project_id, type, title, content, status, source_kind, created_by,
             confidence, source_path, created_at, updated_at)
        VALUES
            ('mn-1', 'default', 'summary', 'Session summary',
             'Important context here.', 'active', 'manual', 'test',
             0.9, 'session.jsonl', '2026-01-01', '2026-01-01')
    """)
    conn_rw.commit()
    conn_rw.close()

    report = build_project_context("myproject", catalog_path, db_path)

    assert report.resolved is True
    assert len(report.memory_nodes) >= 1
    node = next(n for n in report.memory_nodes if n["id"] == "mn-1")
    assert node["title"] == "Session summary"
    assert node["status"] == "active"

    text = format_context_report(report)
    assert "Memory Nodes" in text
    assert "Session summary" in text

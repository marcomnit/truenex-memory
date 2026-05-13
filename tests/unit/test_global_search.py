"""Tests for read-only global search."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from typer.testing import CliRunner

from truenex_memory.cli.main import app
from truenex_memory.ingestion.global_search import (
    build_global_search,
    format_global_search_report,
)
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.source_ledger import upsert_ledger_entry
from truenex_memory.store.sqlite import connect, initialize_schema


runner = CliRunner()


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"task_work_global_search_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _add_chunk(
    db_path: Path,
    *,
    doc_id: str,
    chunk_id: str,
    path: str,
    content: str,
    heading_path: str | None = None,
) -> None:
    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute(
            """
            INSERT INTO documents (
                id, project_id, path, filename, content_hash,
                last_indexed_at, created_at, updated_at
            )
            VALUES (?, 'default', ?, ?, 'hash', '2026-05-04', '2026-05-04', '2026-05-04')
            """,
            (doc_id, path, Path(path).name),
        )
        conn.execute(
            """
            INSERT INTO chunks (
                id, document_id, chunk_index, heading_path, content,
                content_hash, token_count, qdrant_point_id,
                created_at, updated_at
            )
            VALUES (?, ?, 0, ?, ?, 'chunk-hash', 10, NULL, '2026-05-04', '2026-05-04')
            """,
            (chunk_id, doc_id, heading_path, content),
        )
        conn.commit()


def test_global_help_includes_search() -> None:
    result = runner.invoke(app, ["global", "--help"])

    assert result.exit_code == 0
    assert "search" in result.stdout


def test_global_search_missing_db_does_not_create_default_paths() -> None:
    wd = _workdir("missing")
    home = wd / "home"

    result = runner.invoke(app, ["global", "search", "bootstrap", "--home", str(home)])

    assert result.exit_code == 0
    assert "Global Search: bootstrap" in result.stdout
    assert "database not found" in result.stdout
    assert not (home / ".truenex-memory").exists()


def test_global_search_json_finds_unverified_auto_memory_and_chunks() -> None:
    wd = _workdir("json")
    db_path = wd / "memory.db"
    auto_id = MemoryRepository(db_path).add_memory(
        "The global bootstrap must preserve unverified auto memories.",
        memory_type="note",
        title="Global Bootstrap Note",
        status="unverified",
        source_kind="auto",
        source_path=str(wd / "README.md"),
        created_by="auto",
        confidence=0.8,
    )
    _add_chunk(
        db_path,
        doc_id="doc_1",
        chunk_id="doc_1_chunk_0",
        path=str(wd / "docs" / "bootstrap.md"),
        heading_path="Bootstrap",
        content="Bootstrap documentation explains how global search reads indexed chunks.",
    )

    result = runner.invoke(app, [
        "global", "search", "global bootstrap",
        "--db", str(db_path),
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["db_exists"] is True
    assert payload["result_count"] == 2
    assert payload["kind_filter"] == "all"
    memory_results = [item for item in payload["results"] if item["kind"] == "memory_node"]
    assert memory_results[0]["id"] == auto_id
    assert memory_results[0]["status"] == "unverified"
    assert memory_results[0]["confidence"] == 0.8
    assert {item["kind"] for item in payload["results"]} == {"memory_node", "document_chunk"}


def test_global_search_kind_memory_excludes_chunks() -> None:
    wd = _workdir("kind_memory")
    db_path = wd / "memory.db"
    MemoryRepository(db_path).add_memory(
        "Global bootstrap memory result.",
        title="Bootstrap Memory",
        status="unverified",
    )
    _add_chunk(
        db_path,
        doc_id="doc_kind",
        chunk_id="doc_kind_chunk_0",
        path=str(wd / "bootstrap.md"),
        content="Global bootstrap chunk result.",
    )

    result = runner.invoke(app, [
        "global", "search", "global bootstrap",
        "--db", str(db_path),
        "--kind", "memory",
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["kind_filter"] == "memory"
    assert payload["result_count"] == 1
    assert payload["results"][0]["kind"] == "memory_node"


def test_global_search_rejects_invalid_kind() -> None:
    wd = _workdir("bad_kind")
    db_path = wd / "memory.db"
    MemoryRepository(db_path).add_memory("Content", title="Content")

    result = runner.invoke(app, [
        "global", "search", "content",
        "--db", str(db_path),
        "--kind", "notes",
    ])

    assert result.exit_code != 0
    assert "invalid kind" in result.output


def test_global_search_excludes_inactive_memory_by_default() -> None:
    wd = _workdir("inactive")
    db_path = wd / "memory.db"
    repo = MemoryRepository(db_path)
    repo.add_memory(
        "Legacy obsolete sync decision.",
        title="Obsolete Sync",
        status="obsolete",
    )

    default_report = build_global_search(db_path, "obsolete sync")
    inactive_report = build_global_search(db_path, "obsolete sync", include_inactive=True)

    assert default_report.result_count == 0
    assert inactive_report.result_count == 1
    assert inactive_report.results[0].status == "obsolete"


def test_global_search_excludes_skipped_ledger_chunks() -> None:
    wd = _workdir("skipped")
    db_path = wd / "memory.db"
    source_path = str(wd / "session.jsonl")
    _add_chunk(
        db_path,
        doc_id="doc_skipped",
        chunk_id="doc_skipped_chunk_0",
        path=source_path,
        content="Skipped session chunk should not appear in global search.",
    )
    with connect(db_path) as conn:
        upsert_ledger_entry(
            conn,
            "source_skipped",
            source_path,
            "agent_root",
            status="skipped",
            chunk_count=0,
        )

    report = build_global_search(db_path, "skipped session chunk")

    assert report.result_count == 0


def test_global_search_strips_metadata_preamble_from_chunk_results() -> None:
    wd = _workdir("metadata")
    db_path = wd / "memory.db"
    _add_chunk(
        db_path,
        doc_id="doc_meta",
        chunk_id="doc_meta_chunk_0",
        path=str(wd / "README.md"),
        content=(
            "TRUENEX_INGESTION_METADATA\n"
            "source_type: project_docs\n\n"
            "Readable bootstrap content should remain after metadata stripping."
        ),
    )

    report = build_global_search(db_path, "readable bootstrap")
    text = format_global_search_report(report)

    assert report.result_count == 1
    assert report.results[0].content.startswith("Readable bootstrap")
    assert "TRUENEX_INGESTION_METADATA" not in report.results[0].content_excerpt
    assert "Readable bootstrap content" in text


def test_global_search_is_read_only_and_does_not_write_retrieval_logs() -> None:
    wd = _workdir("readonly")
    db_path = wd / "memory.db"
    MemoryRepository(db_path).add_memory(
        "Read-only global search must not record retrieval logs.",
        title="Read Only",
    )
    before_mtime = db_path.stat().st_mtime_ns
    with connect(db_path) as conn:
        before_logs = conn.execute("SELECT COUNT(*) FROM retrieval_logs").fetchone()[0]

    report = build_global_search(db_path, "read only global")

    after_mtime = db_path.stat().st_mtime_ns
    with connect(db_path) as conn:
        after_logs = conn.execute("SELECT COUNT(*) FROM retrieval_logs").fetchone()[0]

    assert report.result_count == 1
    assert after_mtime == before_mtime
    assert after_logs == before_logs

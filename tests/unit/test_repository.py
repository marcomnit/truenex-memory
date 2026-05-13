"""Tests for local SQLite repository behavior."""

import shutil
from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.export import exports_equivalent
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.sqlite import connect


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def test_add_and_search_memory() -> None:
    repo = MemoryRepository(_workdir("repo_search") / "memory.db")
    repo.add_memory("We use SQLite for local metadata.", memory_type="decision")

    results = repo.search("local metadata", top_k=3)

    assert results
    assert results[0].memory_type == "decision"
    assert results[0].status == "active"
    assert "SQLite" in results[0].content


def test_search_excludes_obsolete_by_default() -> None:
    repo = MemoryRepository(_workdir("repo_obsolete") / "memory.db")
    repo.add_memory("Use PostgreSQL for local metadata.", status="obsolete")

    assert repo.search("PostgreSQL metadata") == []


def test_search_can_include_inactive_memories() -> None:
    repo = MemoryRepository(_workdir("repo_include_inactive") / "memory.db")
    repo.add_memory("Use PostgreSQL for local metadata.", status="obsolete")

    results = repo.search("PostgreSQL metadata", include_inactive=True)

    assert results
    assert results[0].status == "obsolete"


def test_list_memory_nodes_and_set_status() -> None:
    repo = MemoryRepository(_workdir("repo_status") / "memory.db")
    memory_id = repo.add_memory("Use Qdrant for vectors.", memory_type="decision")

    repo.set_memory_status(memory_id, "superseded")

    nodes = repo.list_memory_nodes()
    assert len(nodes) == 1
    assert nodes[0].id == memory_id
    assert nodes[0].status == "superseded"
    assert repo.list_memory_nodes(status="superseded")[0].id == memory_id


def test_set_memory_status_validates_status_and_id() -> None:
    repo = MemoryRepository(_workdir("repo_invalid_status") / "memory.db")
    memory_id = repo.add_memory("Use SQLite for metadata.")

    try:
        repo.set_memory_status(memory_id, "invalid")
    except ValueError as exc:
        assert "invalid status" in str(exc)
    else:
        raise AssertionError("expected invalid status to fail")

    try:
        repo.set_memory_status("mem_missing", "obsolete")
    except LookupError as exc:
        assert "memory node not found" in str(exc)
    else:
        raise AssertionError("expected missing memory id to fail")


def test_add_memory_validates_status() -> None:
    repo = MemoryRepository(_workdir("repo_add_invalid_status") / "memory.db")

    try:
        repo.add_memory("Invalid status memory.", status="invalid")
    except ValueError as exc:
        assert "invalid status" in str(exc)
    else:
        raise AssertionError("expected invalid status to fail")


def test_search_records_retrieval_log_with_trace_id() -> None:
    repo = MemoryRepository(_workdir("repo_retrieval_log") / "memory.db")
    repo.add_memory("SQLite stores local metadata.", memory_type="decision")

    results = repo.search("local metadata", top_k=2)

    assert results
    assert repo.last_trace_id is not None
    logs = repo.list_retrieval_logs()
    assert len(logs) == 1
    assert logs[0].id == repo.last_trace_id
    assert logs[0].query == "local metadata"
    assert logs[0].top_k == 2
    assert logs[0].result_count == 1
    assert logs[0].parsed_results()[0]["status"] == "active"
    assert repo.get_retrieval_log(repo.last_trace_id) == logs[0]


def test_retrieval_log_limit_is_validated() -> None:
    repo = MemoryRepository(_workdir("repo_retrieval_log_limit") / "memory.db")

    try:
        repo.list_retrieval_logs(limit=0)
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("expected invalid limit to fail")


def test_export_import_round_trip() -> None:
    workdir = _workdir("repo_export")
    source = MemoryRepository(workdir / "source.db")
    source.add_memory("Qdrant is the planned vector store.", memory_type="decision")
    payload = source.export_data()

    target = MemoryRepository(workdir / "target.db")
    target.import_data(payload)

    assert target.search("planned vector store")


def test_export_import_preserves_full_store_equivalence() -> None:
    workdir = _workdir("repo_export_equivalence")
    source = MemoryRepository(workdir / "source.db")
    decision_id = source.add_memory(
        "Use SQLite for metadata and Qdrant for optional vectors.",
        memory_type="decision",
    )
    source.add_memory("Legacy PostgreSQL plan is obsolete.", memory_type="decision", status="obsolete")
    source.set_memory_status(decision_id, "unverified")

    doc_path = workdir / "architecture.md"
    doc_text = "# Storage\n\nSQLite stores metadata.\n\nQdrant stores optional vectors."
    doc_path.write_text(doc_text, encoding="utf-8")
    source.upsert_document(doc_path, "architecture.md", chunk_text(doc_text))
    source.search("SQLite metadata", top_k=3)

    payload = source.export_data()
    assert payload["schema_migrations"]
    assert payload["retrieval_logs"]
    assert any(row["status"] == "obsolete" for row in payload["memory_nodes"])
    assert any(row["status"] == "unverified" for row in payload["memory_nodes"])

    target = MemoryRepository(workdir / "target.db")
    target.import_data(payload)

    assert exports_equivalent(payload, target.export_data())
    assert target.list_memory_nodes(status="obsolete")[0].content == "Legacy PostgreSQL plan is obsolete."
    assert target.list_retrieval_logs()[0].parsed_results()[0]["status"] in {"active", "unverified"}


def test_upsert_document_uses_logical_filename_when_path_is_temporary() -> None:
    workdir = _workdir("repo_logical_filename")
    temp_path = workdir / "tmpabc123.txt"
    logical_path = str(workdir / "docs" / "architecture.md")
    temp_path.write_text("# Architecture\n\nSQLite stores metadata.", encoding="utf-8")

    repo = MemoryRepository(workdir / "memory.db")
    doc_id = repo.upsert_document(temp_path, logical_path, chunk_text(temp_path.read_text()))

    with connect(repo.db_path) as conn:
        row = conn.execute("SELECT path, filename FROM documents WHERE id = ?", (doc_id,)).fetchone()

    assert row["path"] == logical_path
    assert row["filename"] == "architecture.md"


def test_upsert_document_handles_windows_logical_path_separator() -> None:
    workdir = _workdir("repo_logical_filename_windows")
    temp_path = workdir / "tmpabc123.txt"
    temp_path.write_text("# Guide\n\nWindows logical path.", encoding="utf-8")

    repo = MemoryRepository(workdir / "memory.db")
    doc_id = repo.upsert_document(
        temp_path,
        "docs\\nested\\guide.md",
        chunk_text(temp_path.read_text()),
    )

    with connect(repo.db_path) as conn:
        row = conn.execute("SELECT path, filename FROM documents WHERE id = ?", (doc_id,)).fetchone()

    assert row["path"] == "docs\\nested\\guide.md"
    assert row["filename"] == "guide.md"


def test_upsert_document_uses_fallback_filename_for_blank_logical_path() -> None:
    workdir = _workdir("repo_logical_filename_blank")
    source_path = workdir / "fallback.md"
    source_path.write_text("# Fallback\n\nBlank logical path.", encoding="utf-8")

    repo = MemoryRepository(workdir / "memory.db")
    doc_id = repo.upsert_document(source_path, "  ", chunk_text(source_path.read_text()))

    with connect(repo.db_path) as conn:
        row = conn.execute("SELECT filename FROM documents WHERE id = ?", (doc_id,)).fetchone()

    assert row["filename"] == "fallback.md"


def test_upsert_document_repairs_existing_temporary_filename() -> None:
    workdir = _workdir("repo_repair_filename")
    temp_path = workdir / "tmpold.txt"
    logical_path = str(workdir / "docs" / "runtime.md")
    temp_path.write_text("# Runtime\n\nFirst version.", encoding="utf-8")

    repo = MemoryRepository(workdir / "memory.db")
    doc_id = repo.upsert_document(temp_path, logical_path, chunk_text(temp_path.read_text()))
    with connect(repo.db_path) as conn:
        conn.execute(
            "UPDATE documents SET filename = ? WHERE id = ?",
            ("tmpold.txt", doc_id),
        )
        conn.commit()

    temp_path.write_text("# Runtime\n\nSecond version.", encoding="utf-8")
    repo.upsert_document(temp_path, logical_path, chunk_text(temp_path.read_text()))

    with connect(repo.db_path) as conn:
        row = conn.execute("SELECT filename FROM documents WHERE id = ?", (doc_id,)).fetchone()

    assert row["filename"] == "runtime.md"

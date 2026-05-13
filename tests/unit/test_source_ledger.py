"""Tests for source ledger schema, migration, and domain helpers."""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

from truenex_memory.core.migration import migrate_apply
from truenex_memory.store.sqlite import connect, initialize_schema
from truenex_memory.store.source_ledger import (
    SourceLedgerRecord,
    SOURCE_LEDGER_PHASE3_TRANSITIONS,
    SOURCE_LEDGER_STATUSES,
    get_ledger_entry,
    is_phase3_ledger_transition_allowed,
    list_ledger_entries,
    update_ledger_status,
    upsert_ledger_entry,
)


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / f"ledger_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


# Helpers


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# Valid statuses


def test_source_ledger_statuses_is_frozenset_with_expected_values() -> None:
    assert SOURCE_LEDGER_STATUSES == frozenset(
        {"active", "pending", "error", "missing", "skipped"}
    )


def test_phase3_ledger_transition_policy_matches_design() -> None:
    assert SOURCE_LEDGER_PHASE3_TRANSITIONS == {
        None: frozenset({"active", "skipped", "missing", "error"}),
        "active": frozenset({"active", "skipped", "missing", "error"}),
        "skipped": frozenset({"skipped", "active", "missing", "error"}),
        "missing": frozenset({"missing", "active"}),
        "error": frozenset({"error", "active", "missing"}),
        "pending": frozenset({"active", "skipped", "missing", "error"}),
    }


@pytest.mark.parametrize(
    ("previous_status", "next_status"),
    [
        (None, "active"),
        ("active", "error"),
        ("skipped", "active"),
        ("missing", "active"),
        ("error", "active"),
        ("pending", "missing"),
    ],
)
def test_phase3_ledger_transition_policy_allows_expected_edges(
    previous_status: str | None,
    next_status: str,
) -> None:
    assert is_phase3_ledger_transition_allowed(previous_status, next_status)


@pytest.mark.parametrize(
    ("previous_status", "next_status"),
    [
        ("missing", "skipped"),
        ("error", "skipped"),
        ("unknown", "active"),
        ("active", "pending"),
    ],
)
def test_phase3_ledger_transition_policy_rejects_unplanned_edges(
    previous_status: str | None,
    next_status: str,
) -> None:
    assert not is_phase3_ledger_transition_allowed(previous_status, next_status)


# Schema creation


def test_source_ledger_table_exists_after_initialize_schema() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        initialize_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='source_ledger'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


def test_source_ledger_table_columns_match_spec() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        initialize_schema(conn)
        columns = {
            row["name"]: row["type"]
            for row in conn.execute("PRAGMA table_info(source_ledger)").fetchall()
        }
        assert set(columns) == {
            "source_id",
            "source_path_or_alias",
            "project_name",
            "source_type",
            "parser_version",
            "content_hash",
            "last_modified_at",
            "last_indexed_at",
            "status",
            "error_message",
            "chunk_count",
            "created_at",
            "updated_at",
        }
        assert columns["source_id"] == "TEXT"
        assert columns["source_path_or_alias"] == "TEXT"
        assert columns["project_name"] == "TEXT"
        assert columns["source_type"] == "TEXT"
        assert columns["parser_version"] == "TEXT"
        assert columns["status"] == "TEXT"
        assert columns["content_hash"] == "TEXT"
        assert columns["last_modified_at"] == "TEXT"
        assert columns["last_indexed_at"] == "TEXT"
        assert columns["error_message"] == "TEXT"
        assert columns["chunk_count"] == "INTEGER"
        assert columns["created_at"] == "TEXT"
        assert columns["updated_at"] == "TEXT"
    finally:
        conn.close()


def test_source_ledger_status_check_constraint_rejects_invalid_status() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        initialize_schema(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO source_ledger (
                    source_id, source_path_or_alias, source_type,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("src-1", "/tmp/foo", "project_root", "bogus", "now", "now"),
            )
    finally:
        conn.close()


def test_source_ledger_insert_valid_statuses_succeed() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        initialize_schema(conn)
        for idx, status in enumerate(sorted(SOURCE_LEDGER_STATUSES)):
            conn.execute(
                """
                INSERT INTO source_ledger (
                    source_id, source_path_or_alias, source_type,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"src-{idx}", f"/path/{status}", "project_root", status, "now", "now"),
            )
        conn.commit()
        assert _count_rows(conn, "source_ledger") == len(SOURCE_LEDGER_STATUSES)
    finally:
        conn.close()


def test_source_ledger_defaults_set_correctly() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        initialize_schema(conn)
        conn.execute(
            """
            INSERT INTO source_ledger (
                source_id, source_path_or_alias, source_type,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("src-dfl", "/tmp/dfl", "document", "now", "now"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT status, parser_version, chunk_count FROM source_ledger WHERE source_id = ?",
            ("src-dfl",),
        ).fetchone()
        assert row["status"] == "pending"
        assert row["parser_version"] == "1"
        assert row["chunk_count"] == 0
    finally:
        conn.close()


# Upsert


def test_upsert_ledger_entry_inserts_new_record() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)

    record = upsert_ledger_entry(
        conn,
        "agent_root:abc123",
        "/home/test/project",
        "agent_root",
        project_name="test-project",
        status="active",
        content_hash="deadbeef",
        chunk_count=5,
    )

    assert record.source_id == "agent_root:abc123"
    assert record.source_path_or_alias == "/home/test/project"
    assert record.source_type == "agent_root"
    assert record.project_name == "test-project"
    assert record.status == "active"
    assert record.content_hash == "deadbeef"
    assert record.chunk_count == 5
    assert record.parser_version == "1"
    assert record.created_at == record.updated_at
    assert _count_rows(conn, "source_ledger") == 1
    conn.close()


def test_upsert_ledger_entry_updates_existing_record() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)

    # Insert
    first = upsert_ledger_entry(
        conn,
        "agent_root:abc123",
        "/home/test/project",
        "agent_root",
        status="pending",
        content_hash="aaa",
    )

    # Update same source_id
    import time
    time.sleep(0.01)
    second = upsert_ledger_entry(
        conn,
        "agent_root:abc123",
        "/home/test/project-v2",
        "agent_root",
        status="active",
        content_hash="bbb",
        chunk_count=10,
    )

    assert _count_rows(conn, "source_ledger") == 1
    assert second.source_id == "agent_root:abc123"
    assert second.source_path_or_alias == "/home/test/project-v2"
    assert second.status == "active"
    assert second.content_hash == "bbb"
    assert second.chunk_count == 10
    assert second.created_at == first.created_at  # preserved
    assert second.updated_at != first.updated_at  # refreshed
    conn.close()


def test_upsert_ledger_entry_rejects_invalid_status() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    with pytest.raises(ValueError, match="invalid status"):
        upsert_ledger_entry(
            conn, "id", "/path", "document", status="bogus"
        )
    conn.close()


# Get


def test_get_ledger_entry_returns_none_for_missing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    assert get_ledger_entry(conn, "nonexistent") is None
    conn.close()


def test_get_ledger_entry_returns_record_for_existing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    upsert_ledger_entry(
        conn, "agent_root:abc", "/home/proj", "agent_root", status="active"
    )
    record = get_ledger_entry(conn, "agent_root:abc")
    assert record is not None
    assert record.source_id == "agent_root:abc"
    conn.close()


# List


def test_list_ledger_entries_returns_all() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    upsert_ledger_entry(conn, "a", "/a", "document", status="active")
    upsert_ledger_entry(conn, "b", "/b", "project_root", status="pending")
    upsert_ledger_entry(conn, "c", "/c", "server_alias", status="error")

    all_entries = list_ledger_entries(conn)
    assert len(all_entries) == 3
    conn.close()


def test_list_ledger_entries_filters_by_status() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    upsert_ledger_entry(conn, "a", "/a", "document", status="active")
    upsert_ledger_entry(conn, "b", "/b", "project_root", status="pending")
    upsert_ledger_entry(conn, "c", "/c", "server_alias", status="active")

    active = list_ledger_entries(conn, status="active")
    assert len(active) == 2
    assert all(e.status == "active" for e in active)

    pending = list_ledger_entries(conn, status="pending")
    assert len(pending) == 1
    assert pending[0].source_id == "b"
    conn.close()


def test_list_ledger_entries_filters_by_source_type() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    upsert_ledger_entry(conn, "a", "/a", "document", status="active")
    upsert_ledger_entry(conn, "b", "/b", "project_root", status="pending")
    upsert_ledger_entry(conn, "c", "/c", "document", status="error")

    docs = list_ledger_entries(conn, source_type="document")
    assert len(docs) == 2
    assert all(e.source_type == "document" for e in docs)
    conn.close()


def test_list_ledger_entries_filters_by_both() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    upsert_ledger_entry(conn, "a", "/a", "document", status="active")
    upsert_ledger_entry(conn, "b", "/b", "document", status="error")
    upsert_ledger_entry(conn, "c", "/c", "project_root", status="active")

    result = list_ledger_entries(conn, status="active", source_type="document")
    assert len(result) == 1
    assert result[0].source_id == "a"
    conn.close()


def test_list_ledger_entries_rejects_invalid_status_filter() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    with pytest.raises(ValueError, match="invalid status"):
        list_ledger_entries(conn, status="bogus")
    conn.close()


# Update status


def test_update_ledger_status_changes_status_and_error_message() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    upsert_ledger_entry(conn, "a", "/a", "document", status="pending")

    updated = update_ledger_status(conn, "a", "error", error_message="file not found")
    assert updated.status == "error"
    assert updated.error_message == "file not found"
    conn.close()


def test_update_ledger_status_rejects_invalid_status() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    upsert_ledger_entry(conn, "a", "/a", "document", status="pending")
    with pytest.raises(ValueError, match="invalid status"):
        update_ledger_status(conn, "a", "bogus")
    conn.close()


def test_update_ledger_status_raises_lookup_error_for_missing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    initialize_schema(conn)
    with pytest.raises(LookupError, match="source ledger entry not found"):
        update_ledger_status(conn, "nonexistent", "active")
    conn.close()


# Migration: legacy DB preserves data


def test_migrate_from_v1_legacy_preserves_existing_data_and_adds_ledger() -> None:
    """Simulate a v1 DB with a legacy table, then migrate to v2 and verify
    data is preserved and the source_ledger table exists."""
    workdir = _workdir("migrate_v1_legacy")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"

    # Build a v1-style database: create tables via initialize_schema but
    # then downgrade the schema_migrations marker to v1 and remove
    # source_ledger (simulating a pre-ledger state).
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        initialize_schema(conn)
        # Add legacy user data
        conn.execute(
            "INSERT INTO memories(text, metadata_json) VALUES (?, ?)",
            ("legacy memory", '{"key":"val"}'),
        )
        conn.commit()

        # Fake v1 state: remove source_ledger table, downgrade schema
        conn.execute("DROP TABLE IF EXISTS source_ledger")
        conn.execute("DELETE FROM schema_migrations WHERE version = '2'")
        conn.execute("DELETE FROM schema_migrations WHERE version = '3'")
        conn.execute("DELETE FROM schema_migrations WHERE version = '4'")
        conn.execute("INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES ('1', 'now')")
        conn.commit()

    # Run migration
    result = migrate_apply(db_path, backups_dir)
    assert result["applied"] is True
    assert result["previous_version"] == "1"
    assert result["current_version"] == "4"

    # Verify legacy data survives
    with connect(db_path) as conn:
        row = conn.execute("SELECT text, metadata_json FROM memories").fetchone()
        assert row["text"] == "legacy memory"
        assert row["metadata_json"] == '{"key":"val"}'

        # Verify source_ledger table now exists
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "source_ledger" in tables

        # Verify schema version is 2
        ver = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY CAST(version AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        assert ver["version"] == "4"


def test_migrate_from_v0_legacy_creates_full_schema() -> None:
    """A fresh v0 migration creates the full v2 schema including source_ledger."""
    workdir = _workdir("migrate_v0")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"

    result = migrate_apply(db_path, backups_dir)
    assert result["applied"] is True
    assert result["previous_version"] == "0"
    assert result["current_version"] == "4"

    with connect(db_path) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "source_ledger" in tables
        assert "memories" in tables
        assert "documents" in tables


def test_source_ledger_is_idempotent_on_repeated_initialize_schema() -> None:
    """Calling initialize_schema twice is safe and does not clear data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        initialize_schema(conn)
        upsert_ledger_entry(conn, "a", "/a", "document", status="active")

        # Call initialize_schema again
        initialize_schema(conn)

        # Data must still be there
        record = get_ledger_entry(conn, "a")
        assert record is not None
        assert record.status == "active"
        assert _count_rows(conn, "source_ledger") == 1
    finally:
        conn.close()


# SourceLedgerRecord dataclass


def test_source_ledger_record_is_frozen() -> None:
    record = SourceLedgerRecord(
        source_id="id",
        source_path_or_alias="/path",
        project_name="proj",
        source_type="document",
        parser_version="1",
        content_hash="abc",
        last_modified_at="t1",
        last_indexed_at="t2",
        status="active",
        error_message=None,
        chunk_count=0,
        created_at="t0",
        updated_at="t0",
    )
    assert record.source_id == "id"
    with pytest.raises(Exception):  # frozen dataclass
        record.source_id = "changed"  # type: ignore[misc]

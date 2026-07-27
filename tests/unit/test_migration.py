"""Tests for safe local schema migrations."""

import shutil
import sqlite3
import uuid
from pathlib import Path

from truenex_memory.core.migration import (
    backup_database,
    list_backups,
    migrate_apply,
    migration_status,
    restore_backup,
)
from truenex_memory.store.repository import MemoryRepository


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / f"{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _create_legacy_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE legacy_data (value TEXT NOT NULL)")
        conn.execute("INSERT INTO legacy_data(value) VALUES ('preserve me')")
        conn.commit()


def test_migration_status_does_not_create_missing_database() -> None:
    workdir = _workdir("migration_status")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"

    status = migration_status(db_path)

    assert status == {"current_version": "0", "latest_version": "7", "pending": True}
    assert not db_path.exists()


def test_migrate_apply_creates_schema_without_backup_for_new_database() -> None:
    workdir = _workdir("migration_new")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"

    result = migrate_apply(db_path, backups_dir)

    assert result["applied"] is True
    assert result["previous_version"] == "0"
    assert result["current_version"] == "7"
    assert result["backup_path"] is None
    assert db_path.exists()
    assert list(backups_dir.glob("*.db")) == []


def test_migrate_apply_backs_up_existing_database_before_schema_changes() -> None:
    workdir = _workdir("migration_backup")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"
    _create_legacy_db(db_path)

    result = migrate_apply(db_path, backups_dir)

    assert result["applied"] is True
    assert result["previous_version"] == "0"
    assert result["current_version"] == "7"
    backup_path = Path(str(result["backup_path"]))
    assert backup_path.exists()
    assert backup_path.parent == backups_dir

    with sqlite3.connect(backup_path) as conn:
        row = conn.execute("SELECT value FROM legacy_data").fetchone()
    assert row == ("preserve me",)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM legacy_data").fetchone()
    assert row == ("preserve me",)


def test_migrate_apply_is_idempotent_after_schema_is_current() -> None:
    workdir = _workdir("migration_idempotent")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"

    first = migrate_apply(db_path, backups_dir)
    second = migrate_apply(db_path, backups_dir)

    assert first["applied"] is True
    assert second["applied"] is False
    assert second["current_version"] == "7"
    assert second["pending"] is False
    assert list(backups_dir.glob("*.db")) == []


def test_migrate_apply_preserves_existing_repository_data() -> None:
    workdir = _workdir("migration_preserves_data")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"
    repo = MemoryRepository(db_path)
    repo.add_memory("Migration must preserve memory nodes.", memory_type="decision")
    repo.search("preserve memory", top_k=1)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM schema_migrations")
        conn.commit()

    result = migrate_apply(db_path, backups_dir)

    assert result["applied"] is True
    assert result["previous_version"] == "0"
    assert Path(str(result["backup_path"])).exists()
    restored = MemoryRepository(db_path)
    assert restored.search("preserve memory", top_k=1)
    assert restored.list_retrieval_logs()


# ---------------------------------------------------------------------------
# CLI subcommands
# ---------------------------------------------------------------------------


def test_cli_migrate_help() -> None:
    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    result = CliRunner().invoke(app, ["migrate", "--help"])
    assert result.exit_code == 0
    assert "status" in result.stdout
    assert "apply" in result.stdout


def test_cli_migrate_status_text() -> None:
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_migrate_status_text")
    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(app, ["migrate", "status"])
        assert result.exit_code == 0
        assert "Current schema version:" in result.stdout
        assert "Latest schema version:" in result.stdout
    finally:
        os.chdir(orig_cwd)


def test_cli_migrate_status_json() -> None:
    import json
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_migrate_status_json")
    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(app, ["migrate", "status", "--json"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["current_version"] == "0"
        assert data["latest_version"] == "7"
        assert data["pending"] is True
    finally:
        os.chdir(orig_cwd)


def test_cli_migrate_apply_text_noop() -> None:
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_migrate_apply_noop")
    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        CliRunner().invoke(app, ["migrate", "apply"])
        result2 = CliRunner().invoke(app, ["migrate", "apply"])
        assert result2.exit_code == 0
        assert "Already up to date" in result2.stdout
    finally:
        os.chdir(orig_cwd)


def test_cli_migrate_apply_json() -> None:
    import json
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_migrate_apply_json")
    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(app, ["migrate", "apply", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["applied"] is True
        assert data["previous_version"] == "0"
        assert data["current_version"] == "7"
    finally:
        os.chdir(orig_cwd)


# ---------------------------------------------------------------------------
# list_backups / restore_backup primitives
# ---------------------------------------------------------------------------


def test_list_backups_empty_dir() -> None:
    workdir = _workdir("list_backups_empty")
    backups_dir = workdir / "backups"
    backups_dir.mkdir(parents=True)

    result = list_backups(backups_dir)
    assert result == []


def test_list_backups_missing_dir_returns_empty() -> None:
    workdir = _workdir("list_backups_missing")
    backups_dir = workdir / "nonexistent"

    result = list_backups(backups_dir)
    assert result == []


def test_list_backups_returns_entries_sorted_newest_first() -> None:
    import time

    workdir = _workdir("list_backups_sorted")
    backups_dir = workdir / "backups"
    backups_dir.mkdir(parents=True)

    # Create files with names simulating timestamp order
    older = backups_dir / "truenex_memory_20260501T000000000000Z.db"
    newer = backups_dir / "truenex_memory_20260502T000000000000Z.db"
    older.write_bytes(b"older")
    newer.write_bytes(b"newer")
    time.sleep(0.01)  # Ensure ctime ordering matches name ordering

    result = list_backups(backups_dir)

    assert len(result) == 2
    assert result[0]["filename"] == "truenex_memory_20260502T000000000000Z.db"
    assert result[1]["filename"] == "truenex_memory_20260501T000000000000Z.db"
    for entry in result:
        assert "filename" in entry
        assert "path" in entry
        assert "size_bytes" in entry
        assert "created" in entry


def test_list_backups_ignores_non_db_files() -> None:
    workdir = _workdir("list_backups_filter")
    backups_dir = workdir / "backups"
    backups_dir.mkdir(parents=True)
    (backups_dir / "truenex_memory_20260501T000000000000Z.db").write_bytes(b"data")
    (backups_dir / "notes.txt").write_text("not a backup")

    result = list_backups(backups_dir)
    assert len(result) == 1
    assert result[0]["filename"].endswith(".db")


def test_restore_backup_creates_db_from_backup() -> None:
    workdir = _workdir("restore_create")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True)

    _create_legacy_db(db_path)
    backup_path = backup_database(db_path, backups_dir)
    assert backup_path is not None
    backup_filename = backup_path.name

    # Restore into a fresh path where no database exists
    fresh_db = workdir / ".truenex-memory-restored" / "truenex_memory.db"
    result = restore_backup(fresh_db, backups_dir, backup_filename)

    assert result["restored"] is True
    assert result["backup_filename"] == backup_filename
    assert result["safety_backup_path"] is None
    assert fresh_db.exists()

    with sqlite3.connect(fresh_db) as conn:
        row = conn.execute("SELECT value FROM legacy_data").fetchone()
    assert row == ("preserve me",)


def test_restore_backup_creates_safety_backup_before_overwrite() -> None:
    workdir = _workdir("restore_safety")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True)

    _create_legacy_db(db_path)
    backup_path = backup_database(db_path, backups_dir)
    assert backup_path is not None
    backup_filename = backup_path.name

    # Modify the current DB so we can tell it apart from the backup
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE legacy_data SET value = 'modified'")
        conn.commit()

    result = restore_backup(db_path, backups_dir, backup_filename)

    assert result["restored"] is True
    assert result["safety_backup_path"] is not None
    assert Path(str(result["safety_backup_path"])).exists()

    # Restored DB should have original data from the backup
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM legacy_data").fetchone()
    assert row == ("preserve me",)

    # Safety backup should have the modified data
    with sqlite3.connect(str(result["safety_backup_path"])) as conn:
        row = conn.execute("SELECT value FROM legacy_data").fetchone()
    assert row == ("modified",)


def test_restore_backup_rejects_path_traversal() -> None:
    workdir = _workdir("restore_traversal")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True)

    import pytest

    with pytest.raises(ValueError, match="filename|escapes backups_dir"):
        restore_backup(db_path, backups_dir, "..\\..\\etc\\passwd")


def test_restore_backup_rejects_non_db_file() -> None:
    import pytest

    workdir = _workdir("restore_non_db")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True)
    (backups_dir / "not-a-db.txt").write_text("not a database", encoding="utf-8")

    with pytest.raises(ValueError, match=".db file"):
        restore_backup(db_path, backups_dir, "not-a-db.txt")


def test_restore_backup_rejects_nonexistent_file() -> None:
    import pytest

    workdir = _workdir("restore_missing")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="Backup not found"):
        restore_backup(db_path, backups_dir, "nonexistent.db")


def test_restore_backup_reads_correct_schema_version() -> None:
    workdir = _workdir("restore_version")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True)

    # Create a migrated DB and back it up
    migrate_apply(db_path, backups_dir)
    backup_path = backup_database(db_path, backups_dir)
    assert backup_path is not None
    backup_filename = backup_path.name

    # Restore into a fresh path
    fresh_db = workdir / ".truenex-memory-restored" / "truenex_memory.db"
    result = restore_backup(fresh_db, backups_dir, backup_filename)

    assert result["current_version"] == "7"


# ---------------------------------------------------------------------------
# CLI: migrate backup-list / restore
# ---------------------------------------------------------------------------


def test_cli_migrate_help_includes_new_commands() -> None:
    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    result = CliRunner().invoke(app, ["migrate", "--help"])
    assert result.exit_code == 0
    assert "backup-list" in result.stdout
    assert "restore" in result.stdout


def test_cli_migrate_backup_list_text_empty() -> None:
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_backup_list_text")
    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(app, ["migrate", "backup-list"])
        assert result.exit_code == 0
        assert "No migration backups found" in result.stdout
    finally:
        os.chdir(orig_cwd)


def test_cli_migrate_backup_list_text_with_backups() -> None:
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_backup_list_text2")
    (workdir / ".truenex-memory").mkdir(parents=True, exist_ok=True)
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    (backups_dir / "truenex_memory_20260501T000000000000Z.db").write_bytes(b"data")

    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(app, ["migrate", "backup-list"])
        assert result.exit_code == 0
        assert "truenex_memory_20260501T000000000000Z.db" in result.stdout
        assert "KiB" in result.stdout
    finally:
        os.chdir(orig_cwd)


def test_cli_migrate_backup_list_json() -> None:
    import json
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_backup_list_json")
    (workdir / ".truenex-memory").mkdir(parents=True, exist_ok=True)
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    (backups_dir / "my_backup.db").write_bytes(b"test")

    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(app, ["migrate", "backup-list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["filename"] == "my_backup.db"
        assert "size_bytes" in data[0]
        assert "created" in data[0]
    finally:
        os.chdir(orig_cwd)


def test_cli_migrate_restore_text() -> None:
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_restore_text")
    (workdir / ".truenex-memory").mkdir(parents=True, exist_ok=True)
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"

    _create_legacy_db(db_path)
    backup_path = backup_database(db_path, backups_dir)
    assert backup_path is not None
    backup_filename = backup_path.name

    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(
            app, ["migrate", "restore", backup_filename]
        )
        assert result.exit_code == 0
        assert "Restored:" in result.stdout
        assert backup_filename in result.stdout
        assert "Safety backup:" in result.stdout
    finally:
        os.chdir(orig_cwd)


def test_cli_migrate_restore_json() -> None:
    import json
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_restore_json")
    (workdir / ".truenex-memory").mkdir(parents=True, exist_ok=True)
    backups_dir = workdir / ".truenex-memory" / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"

    _create_legacy_db(db_path)
    backup_path = backup_database(db_path, backups_dir)
    assert backup_path is not None
    backup_filename = backup_path.name

    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(
            app, ["migrate", "restore", backup_filename, "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["restored"] is True
        assert data["backup_filename"] == backup_filename
        assert "safety_backup_path" in data
        assert "current_version" in data
    finally:
        os.chdir(orig_cwd)


def test_cli_migrate_restore_rejects_missing_backup() -> None:
    import json
    import os

    from typer.testing import CliRunner

    from truenex_memory.cli.main import app

    workdir = _workdir("cli_restore_missing")
    (workdir / ".truenex-memory" / "backups").mkdir(parents=True, exist_ok=True)

    orig_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(
            app, ["migrate", "restore", "no_such_backup.db", "--json"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert "error" in data
    finally:
        os.chdir(orig_cwd)


def test_initialize_upgrades_v5_db_to_v6_with_secondary_indexes(tmp_path) -> None:
    """A v5 database gains the v6 secondary indexes on the next
    initialize_schema, without re-running the v5 FTS backfill, and the
    upgrade is idempotent."""
    from truenex_memory.store.sqlite import connect, initialize_schema

    db_path = tmp_path / "memory.db"
    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute(
            """
            INSERT INTO documents (
                id, project_id, path, filename, content_hash,
                last_indexed_at, created_at, updated_at
            ) VALUES ('doc_1', 'default', 'docs/a.md', 'a.md', 'h',
                      'now', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO chunks (
                id, document_id, chunk_index, heading_path, content,
                content_hash, token_count, created_at, updated_at
            ) VALUES ('doc_1_chunk_0', 'doc_1', 0, NULL, 'alpha content',
                      'ch', 2, 'now', 'now')
            """
        )
        conn.commit()
        # Simulate the v5 end-state: no v6+ row, no secondary indexes.
        conn.execute("DROP INDEX IF EXISTS idx_chunks_document_id")
        conn.execute("DROP INDEX IF EXISTS idx_source_ledger_path")
        conn.execute("DROP INDEX IF EXISTS idx_documents_path")
        conn.execute("DROP INDEX IF EXISTS idx_chunks_qdrant_point")
        conn.execute("DROP INDEX IF EXISTS idx_chunks_embedding_model")
        conn.execute("DELETE FROM schema_migrations WHERE version IN ('6', '7')")
        conn.commit()
        fts_count_before = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]

    with connect(db_path) as conn:
        initialize_schema(conn)
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert {
            "idx_chunks_document_id",
            "idx_source_ledger_path",
            "idx_documents_path",
            "idx_chunks_qdrant_point",
        } <= indexes
        versions = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        # v5 stays recorded (its FTS backfill is NOT re-run) and the current
        # schema version (v7, which also creates the v6 indexes) is added.
        assert "5" in versions
        assert "7" in versions
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == fts_count_before

        # Idempotent: a second initialize keeps everything stable.
        initialize_schema(conn)
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == fts_count_before
        versions_again = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert versions_again == versions


def test_initialize_upgrades_v6_db_to_v7_with_point_id_index(tmp_path) -> None:
    """A v6 database gains idx_chunks_qdrant_point on the next
    initialize_schema, without any FTS rebuild, and the upgrade is
    idempotent."""
    from truenex_memory.store.sqlite import connect, initialize_schema

    db_path = tmp_path / "memory.db"
    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute(
            """
            INSERT INTO documents (
                id, project_id, path, filename, content_hash,
                last_indexed_at, created_at, updated_at
            ) VALUES ('doc_1', 'default', 'docs/a.md', 'a.md', 'h',
                      'now', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO chunks (
                id, document_id, chunk_index, heading_path, content,
                content_hash, token_count, created_at, updated_at
            ) VALUES ('doc_1_chunk_0', 'doc_1', 0, NULL, 'alpha content',
                      'ch', 2, 'now', 'now')
            """
        )
        conn.commit()
        # Simulate the v6 end-state: v6 row recorded, no v7 row, no
        # point-id/model indexes.
        conn.execute("DROP INDEX IF EXISTS idx_chunks_qdrant_point")
        conn.execute("DROP INDEX IF EXISTS idx_chunks_embedding_model")
        conn.execute("DELETE FROM schema_migrations WHERE version = '7'")
        conn.execute(
            "INSERT OR REPLACE INTO schema_migrations(version, applied_at) "
            "VALUES ('6', datetime('now'))"
        )
        conn.commit()
        fts_count_before = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]

    with connect(db_path) as conn:
        initialize_schema(conn)
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert "idx_chunks_qdrant_point" in indexes
        assert "idx_chunks_embedding_model" in indexes
        versions = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert {"5", "6", "7"} <= versions
        # No FTS rebuild happened.
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == fts_count_before

        # Idempotent: a second initialize keeps everything stable.
        initialize_schema(conn)
        versions_again = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert versions_again == versions


def test_initialize_self_heals_empty_fts_with_recorded_v5(tmp_path) -> None:
    """v5 row recorded but the FTS index empty (e.g. v5 applied on a Python
    build without FTS5, or a manual DROP TABLE chunks_fts) while chunks are
    populated: initialize_schema must re-run the backfill."""
    from truenex_memory.store.sqlite import (
        chunks_fts_available,
        connect,
        initialize_schema,
    )

    db_path = tmp_path / "memory.db"
    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute(
            """
            INSERT INTO documents (
                id, project_id, path, filename, content_hash,
                last_indexed_at, created_at, updated_at
            ) VALUES ('doc_1', 'default', 'docs/a.md', 'a.md', 'h',
                      'now', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO chunks (
                id, document_id, chunk_index, heading_path, content,
                content_hash, token_count, created_at, updated_at
            ) VALUES ('doc_1_chunk_0', 'doc_1', 0, NULL, 'alpha content',
                      'ch', 2, 'now', 'now')
            """
        )
        conn.commit()
        # Undo the FTS index without touching schema_migrations: v5 stays
        # recorded while the backfill is effectively gone.
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS chunks_fts_ai;
            DROP TRIGGER IF EXISTS chunks_fts_ad;
            DROP TRIGGER IF EXISTS chunks_fts_au;
            DROP TABLE IF EXISTS chunks_fts;
            """
        )
        conn.commit()
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = '5'"
        ).fetchone() is not None

    with connect(db_path) as conn:
        initialize_schema(conn)
        assert chunks_fts_available(conn)
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 1

        # Idempotent: no repeated backfill once the index is populated.
        initialize_schema(conn)
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 1

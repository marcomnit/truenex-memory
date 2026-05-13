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

    assert status == {"current_version": "0", "latest_version": "4", "pending": True}
    assert not db_path.exists()


def test_migrate_apply_creates_schema_without_backup_for_new_database() -> None:
    workdir = _workdir("migration_new")
    db_path = workdir / ".truenex-memory" / "truenex_memory.db"
    backups_dir = workdir / ".truenex-memory" / "backups"

    result = migrate_apply(db_path, backups_dir)

    assert result["applied"] is True
    assert result["previous_version"] == "0"
    assert result["current_version"] == "4"
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
    assert result["current_version"] == "4"
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
    assert second["current_version"] == "4"
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
        assert data["latest_version"] == "4"
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
        assert data["current_version"] == "4"
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

    assert result["current_version"] == "4"


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

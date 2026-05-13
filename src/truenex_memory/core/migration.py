"""Local schema migration support.

Provides safe, idempotent migration primitives with automatic pre-migration
database backup, backup listing, and local restore with a safety backup before
overwrite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3

from truenex_memory.release.version import DB_SCHEMA_VERSION
from truenex_memory.store.sqlite import connect, initialize_schema, apply_column_upgrades


MigrationStatus = dict[str, object]
MigrationResult = dict[str, object]
BackupEntry = dict[str, object]


def get_current_schema_version(conn: sqlite3.Connection) -> str:
    """Return the highest schema version applied in the database.

    Returns ``"0"`` when no migrations have been applied (missing table or row).
    """
    try:
        row = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY CAST(version AS INTEGER) DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return "0"
    return row["version"] if row else "0"


def get_latest_schema_version() -> str:
    """Return the schema version defined in the current code."""
    return DB_SCHEMA_VERSION


def migration_status(db_path: Path) -> MigrationStatus:
    """Return a dict with *current_version*, *latest_version*, and *pending*.

    Does **not** create the database file when it does not exist.
    """
    if not db_path.exists():
        return {
            "current_version": "0",
            "latest_version": get_latest_schema_version(),
            "pending": True,
        }

    with connect(db_path) as conn:
        current = get_current_schema_version(conn)

    return {
        "current_version": current,
        "latest_version": get_latest_schema_version(),
        "pending": current != get_latest_schema_version(),
    }


def backup_database(db_path: Path, backups_dir: Path) -> Path | None:
    """Copy the database file into *backups_dir* with a UTC timestamp suffix.

    Returns the backup path, or ``None`` when *db_path* does not exist.
    """
    if not db_path.exists():
        return None

    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_name = f"{db_path.stem}_{timestamp}{db_path.suffix}"
    backup_path = backups_dir / backup_name
    shutil.copy2(db_path, backup_path)
    return backup_path


def migrate_apply(db_path: Path, backups_dir: Path) -> MigrationResult:
    """Apply pending schema migrations.

    1. Checks whether migrations are pending.
    2. Creates a timestamped backup of the database (when it exists) **before**
       applying any changes.
    3. Runs ``initialize_schema`` idempotently (``CREATE TABLE IF NOT EXISTS``,
       ``INSERT OR IGNORE`` for ``schema_migrations``).

    Returns a JSON-safe dict with previous/current/latest versions,
    *backup_path* (``None`` when no backup was taken), and *applied*.
    """
    before = migration_status(db_path)
    if not before["pending"]:
        return {
            **before,
            "previous_version": before["current_version"],
            "backup_path": None,
            "applied": False,
        }

    backup_path = backup_database(db_path, backups_dir) if db_path.exists() else None

    with connect(db_path) as conn:
        initialize_schema(conn)
        apply_column_upgrades(conn)

    after = migration_status(db_path)
    return {
        **after,
        "previous_version": before["current_version"],
        "backup_path": str(backup_path) if backup_path else None,
        "applied": True,
    }


def list_backups(backups_dir: Path) -> list[BackupEntry]:
    """Return a list of migration backups sorted newest-first.

    Each entry is a JSON-safe dict with *filename*, *path*, *size_bytes*,
    and *created* (ISO-8601 UTC timestamp from the filesystem).
    """
    if not backups_dir.exists():
        return []

    entries: list[BackupEntry] = []
    for f in sorted(backups_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if not f.is_file() or f.suffix != ".db":
            continue
        stat = f.stat()
        entries.append(
            {
                "filename": f.name,
                "path": str(f.resolve()),
                "size_bytes": stat.st_size,
                "created": datetime.fromtimestamp(
                    stat.st_ctime, tz=timezone.utc
                ).isoformat(),
            }
        )
    return entries


def restore_backup(
    db_path: Path, backups_dir: Path, backup_filename: str
) -> MigrationResult:
    """Restore *backup_filename* from *backups_dir* onto *db_path*.

    1. Resolves the backup path and validates it stays inside *backups_dir*
       (path-traversal rejection).
    2. Confirms the backup file exists.
    3. Creates a pre-restore safety backup of the current database (if it
       exists) before overwriting.
    4. Copies the backup file over the active database.
    5. Verifies the restored database is readable.

    Returns a JSON-safe dict with *restored*, *backup_filename*, *db_path*,
    *safety_backup_path* (``None`` when no safety backup was taken), and
    the *current_version* read from the restored database.
    """
    backup_name = Path(backup_filename)
    if backup_name.name != backup_filename:
        raise ValueError(f"Backup name must be a filename: {backup_filename!r}")
    if backup_name.suffix != ".db":
        raise ValueError(f"Backup must be a .db file: {backup_filename!r}")

    backups_dir = backups_dir.resolve()
    backup_path = (backups_dir / backup_filename).resolve()
    try:
        backup_path.relative_to(backups_dir)
    except ValueError as exc:
        raise ValueError(f"Backup filename escapes backups_dir: {backup_filename!r}") from exc

    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")
    if not backup_path.is_file():
        raise ValueError(f"Backup is not a file: {backup_path}")

    # Pre-restore safety backup
    safety_backup_path: str | None = None
    if db_path.exists():
        safety = backup_database(db_path, backups_dir)
        if safety is not None:
            safety_backup_path = str(safety.resolve())

    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, db_path)

    # Verify the restored database is readable
    try:
        with connect(db_path) as conn:
            current = get_current_schema_version(conn)
    except Exception as exc:
        raise RuntimeError(f"Restored database is not readable: {exc}") from exc

    return {
        "restored": True,
        "backup_filename": backup_filename,
        "db_path": str(db_path.resolve()),
        "safety_backup_path": safety_backup_path,
        "current_version": current,
    }

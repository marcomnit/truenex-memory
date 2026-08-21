"""SQLite schema management."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any


# Single source for the schema version. It used to be declared here AND in
# release.version, which meant bumping one and not the other left the store
# permanently "pending": migration_status compared against one constant
# while initialize_schema recorded the other, so `migrate apply` re-ran the
# column upgrades on every invocation and never settled.
from truenex_memory.release.version import DB_SCHEMA_VERSION as SCHEMA_VERSION
# Schema version that introduced the chunks_fts external-content index and
# its backfill.  The FTS rebuild must trigger only when THIS version's work
# is pending, not on every later version bump (an index-only migration
# must not reindex hundreds of thousands of chunks).
FTS_INTRODUCED_VERSION = "5"


@dataclass(frozen=True)
class MemoryRecord:
    """Simple record used by the compatibility SQLite memory store."""

    id: int
    text: str
    metadata: dict[str, Any]


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with local-first defaults."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA recursive_triggers = ON")
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create or safely upgrade the local schema."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          path TEXT NOT NULL,
          filename TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          last_indexed_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
          id TEXT PRIMARY KEY,
          document_id TEXT NOT NULL,
          chunk_index INTEGER NOT NULL,
          heading_path TEXT,
          content TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          token_count INTEGER NOT NULL DEFAULT 0,
          qdrant_point_id TEXT,
          embedding_model TEXT,
          embedding_vector_json TEXT,
          source_type TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memory_nodes (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          type TEXT NOT NULL,
          title TEXT NOT NULL,
          content TEXT NOT NULL,
          status TEXT NOT NULL,
          source_kind TEXT NOT NULL,
          source_document_id TEXT,
          source_chunk_id TEXT,
          source_path TEXT,
          content_hash TEXT,
          created_by TEXT NOT NULL,
          model_name TEXT,
          confidence REAL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          superseded_by TEXT
        );

        CREATE TABLE IF NOT EXISTS edges (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          source_node_id TEXT NOT NULL,
          target_node_id TEXT NOT NULL,
          relation_type TEXT NOT NULL,
          created_by TEXT NOT NULL,
          confidence REAL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS retrieval_logs (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          query TEXT NOT NULL,
          top_k INTEGER NOT NULL,
          result_count INTEGER NOT NULL,
          results_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS schema_migrations (
          version TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memories (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          text TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS source_ledger (
          source_id TEXT PRIMARY KEY,
          source_path_or_alias TEXT NOT NULL,
          project_name TEXT,
          source_type TEXT NOT NULL,
          parser_version TEXT NOT NULL DEFAULT '1',
          content_hash TEXT,
          last_modified_at TEXT,
          last_indexed_at TEXT,
          status TEXT NOT NULL DEFAULT 'pending'
              CHECK(status IN ('active','pending','error','missing','skipped')),
          error_message TEXT,
          chunk_count INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
          task_id    TEXT PRIMARY KEY,
          title      TEXT NOT NULL,
          type       TEXT NOT NULL CHECK(type IN ('bugfix','feature','refactor','review','query')),
          project    TEXT,
          agent_session_id TEXT,
          human_outcome    INTEGER CHECK(human_outcome IN (1, 0, -1)),
          human_comment    TEXT,
          total_tokens     INTEGER,
          total_duration_s REAL,
          status     TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed','unrated')),
          created_at TEXT NOT NULL,
          closed_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS task_steps (
          step_id       TEXT PRIMARY KEY,
          task_id       TEXT NOT NULL,
          step_index    INTEGER NOT NULL,
          prompt_used   TEXT,
          output        TEXT,
          brain_judgment TEXT CHECK(brain_judgment IN ('ok','needs_revision','rejected')),
          tokens_used   INTEGER,
          duration_s    REAL,
          model_used    TEXT,
          created_at    TEXT NOT NULL,
          FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS verifier_rounds (
          round_id         TEXT PRIMARY KEY,
          task_id          TEXT NOT NULL,
          step_id          TEXT,
          suggestion_type  TEXT NOT NULL,
          brain_accepted   INTEGER NOT NULL CHECK(brain_accepted IN (0, 1)),
          brain_rationale  TEXT,
          created_at       TEXT NOT NULL,
          FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
        );

        -- Secondary indexes (schema v6): without them, per-document
        -- deletes (ledger purge) and per-path ledger lookups (global
        -- search FTS candidate filtering) degenerate into full table
        -- scans on large stores.
        CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
        CREATE INDEX IF NOT EXISTS idx_source_ledger_path ON source_ledger(source_path_or_alias);
        CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path);
        -- Schema v7: dense-search hydration resolves chunks by
        -- qdrant_point_id in batches; without this index each lookup is a
        -- full table scan on large stores. idx_chunks_embedding_model is a
        -- covering index for the per-search cache-validation aggregate
        -- (COUNT, MAX(updated_at) by model): without it the aggregate
        -- touches every embedded row (measured: seconds per query).
        CREATE INDEX IF NOT EXISTS idx_chunks_qdrant_point ON chunks(qdrant_point_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_embedding_model ON chunks(embedding_model, updated_at);
        """
    )
    _ensure_column(conn, "chunks", "embedding_model", "TEXT")
    _ensure_column(conn, "chunks", "embedding_vector_json", "TEXT")
    fts_available = _ensure_chunks_fts(conn)
    fts_migration_pending = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (FTS_INTRODUCED_VERSION,),
    ).fetchone() is None
    fts_backfill_needed = fts_migration_pending
    if fts_available and not fts_migration_pending:
        # Self-heal: the v5 row may be recorded while the backfill never
        # happened or was undone (e.g. a Python build without FTS5 at v5
        # time, or a manual DROP TABLE chunks_fts).  Rebuild when the FTS
        # index is empty but chunks exist.  LIMIT 1 probes are O(1)-ish,
        # unlike COUNT(*) on the FTS index.  Fresh empty databases have no
        # chunks, so they never trigger a spurious backfill.
        chunks_populated = conn.execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is not None
        if chunks_populated:
            fts_empty = conn.execute("SELECT 1 FROM chunks_fts LIMIT 1").fetchone() is None
            fts_backfill_needed = fts_empty
    if fts_available and fts_backfill_needed:
        rebuild_chunks_fts(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
            (FTS_INTRODUCED_VERSION,),
        )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def chunks_fts_available(conn: sqlite3.Connection) -> bool:
    """Return whether the persistent chunk FTS5 index is available."""

    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chunks_fts'"
    ).fetchone()
    return row is not None


def rebuild_chunks_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the disposable FTS index from canonical chunk rows."""

    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")


def _ensure_chunks_fts(conn: sqlite3.Connection) -> bool:
    """Create the FTS5 external-content index and synchronization triggers."""

    try:
        conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
              content,
              heading_path,
              content='chunks',
              content_rowid='rowid',
              tokenize='unicode61 remove_diacritics 2'
            );

            CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
              INSERT INTO chunks_fts(rowid, content, heading_path)
              VALUES (new.rowid, new.content, COALESCE(new.heading_path, ''));
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
              INSERT INTO chunks_fts(chunks_fts, rowid, content, heading_path)
              VALUES ('delete', old.rowid, old.content, COALESCE(old.heading_path, ''));
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_fts_au AFTER UPDATE ON chunks BEGIN
              INSERT INTO chunks_fts(chunks_fts, rowid, content, heading_path)
              VALUES ('delete', old.rowid, old.content, COALESCE(old.heading_path, ''));
              INSERT INTO chunks_fts(rowid, content, heading_path)
              VALUES (new.rowid, new.content, COALESCE(new.heading_path, ''));
            END;
            """
        )
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower() or "no such module" in str(exc).lower():
            return False
        raise
    return True


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def apply_column_upgrades(conn: sqlite3.Connection) -> None:
    """Add columns introduced in schema upgrades to existing tables.

    Uses try/except because SQLite has no IF NOT EXISTS for ALTER TABLE.
    Safe to call repeatedly — duplicate-column errors are ignored.
    """
    upgrades = [
        "ALTER TABLE chunks ADD COLUMN source_type TEXT",
        # Which memory replaced this one. The `superseded` status already
        # existed but said nothing about the replacement, so a reader could
        # see that a note was retired without being able to find what now
        # holds true. Measured before this: 4 nodes carried the status and
        # none could be followed to a successor.
        "ALTER TABLE memory_nodes ADD COLUMN superseded_by TEXT",
    ]
    for sql in upgrades:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists


class SQLiteMemoryStore:
    """Small local text store kept for focused Task 2 tests."""

    def __init__(self, database: str | Path) -> None:
        self.database = database
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> "SQLiteMemoryStore":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._connection is not None:
            return
        if self.database == ":memory:":
            conn = sqlite3.connect(":memory:")
        else:
            conn = connect(Path(self.database))
        conn.row_factory = sqlite3.Row
        initialize_schema(conn)
        self._connection = conn

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> MemoryRecord:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("text cannot be empty")
        conn = self._conn()
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        cursor = conn.execute(
            "INSERT INTO memories(text, metadata_json) VALUES (?, ?)",
            (clean_text, metadata_json),
        )
        conn.commit()
        return MemoryRecord(id=int(cursor.lastrowid), text=clean_text, metadata=dict(metadata or {}))

    def search(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        tokens = [token.lower() for token in query.split() if token.strip()]
        if not tokens:
            return []
        rows = self._conn().execute("SELECT id, text, metadata_json FROM memories ORDER BY id").fetchall()
        results: list[MemoryRecord] = []
        for row in rows:
            text = str(row["text"])
            lowered = text.lower()
            if all(token in lowered for token in tokens):
                results.append(
                    MemoryRecord(
                        id=int(row["id"]),
                        text=text,
                        metadata=json.loads(row["metadata_json"]),
                    )
                )
            if len(results) >= limit:
                break
        return results

    def schema_version(self) -> int:
        row = self._conn().execute(
            "SELECT version FROM schema_migrations ORDER BY CAST(version AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        return int(row["version"]) if row else 0

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            self.open()
        assert self._connection is not None
        return self._connection

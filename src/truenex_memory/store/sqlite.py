"""SQLite schema management."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any


SCHEMA_VERSION = "4"


@dataclass(frozen=True)
class MemoryRecord:
    """Simple record used by the compatibility SQLite memory store."""

    id: int
    text: str
    metadata: dict[str, Any]


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with local-first defaults."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create the v1 local schema if it does not already exist."""

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
          updated_at TEXT NOT NULL
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
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
        (SCHEMA_VERSION,),
    )
    _ensure_column(conn, "chunks", "embedding_model", "TEXT")
    _ensure_column(conn, "chunks", "embedding_vector_json", "TEXT")
    conn.commit()


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

"""Tests for the persistent SQLite FTS5 chunk index."""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.store.repository import MemoryRepository, _search_chunks
from truenex_memory.store.sqlite import (
    chunks_fts_available,
    connect,
    initialize_schema,
)


def _drop_fts(conn) -> None:
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS chunks_fts_ai;
        DROP TRIGGER IF EXISTS chunks_fts_ad;
        DROP TRIGGER IF EXISTS chunks_fts_au;
        DROP TABLE IF EXISTS chunks_fts;
        """
    )


def test_schema_v5_backfills_existing_chunks(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    doc_path = tmp_path / "meddesk.md"
    doc_path.write_text("MedDesk legacy key rotation procedure.", encoding="utf-8")
    repository = MemoryRepository(db_path)
    repository.upsert_document(
        doc_path,
        "docs/meddesk.md",
        chunk_text(doc_path.read_text(encoding="utf-8")),
    )

    with connect(db_path) as conn:
        _drop_fts(conn)
        conn.execute("DELETE FROM schema_migrations WHERE version = '5'")
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES ('4', datetime('now'))"
        )
        conn.commit()
        initialize_schema(conn)
        assert chunks_fts_available(conn)
        count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        version = conn.execute(
            "SELECT MAX(CAST(version AS INTEGER)) FROM schema_migrations"
        ).fetchone()[0]

    assert count == 1
    assert version == 7
    assert repository.search("MedDesk rotation", top_k=3)[0].source_path == "docs/meddesk.md"


def test_fts_triggers_replace_old_document_content(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    doc_path = tmp_path / "notes.md"
    repository = MemoryRepository(db_path)
    doc_path.write_text("alphatermunique deployment note", encoding="utf-8")
    repository.upsert_document(
        doc_path,
        "docs/notes.md",
        chunk_text(doc_path.read_text(encoding="utf-8")),
    )

    doc_path.write_text("betatermunique deployment note", encoding="utf-8")
    repository.upsert_document(
        doc_path,
        "docs/notes.md",
        chunk_text(doc_path.read_text(encoding="utf-8")),
    )

    assert repository.search("alphatermunique", top_k=3) == []
    beta_results = repository.search("betatermunique", top_k=3)
    assert beta_results
    assert "betatermunique" in beta_results[0].content


def test_python_bm25_remains_fallback_without_fts_table(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    doc_path = tmp_path / "fallback.md"
    doc_path.write_text("SQLite fallback remains available.", encoding="utf-8")
    repository = MemoryRepository(db_path)
    repository.upsert_document(
        doc_path,
        "docs/fallback.md",
        chunk_text(doc_path.read_text(encoding="utf-8")),
    )

    with connect(db_path) as conn:
        _drop_fts(conn)
        hits = _search_chunks(conn, {"sqlite", "fallback"}, limit=10)

    assert hits
    assert hits[0].source_path == "docs/fallback.md"


def test_search_hides_ingestion_metadata_chunks(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    repository = MemoryRepository(db_path)
    metadata_path = tmp_path / "metadata.md"
    content_path = tmp_path / "content.md"
    metadata_path.write_text(
        'TRUENEX_INGESTION_METADATA {"project": "MedDesk", "topic": "rotation"}',
        encoding="utf-8",
    )
    content_path.write_text("MedDesk rotation procedure for the public key.", encoding="utf-8")
    repository.upsert_document(metadata_path, "docs/metadata.md", chunk_text(metadata_path.read_text(encoding="utf-8")))
    repository.upsert_document(content_path, "docs/content.md", chunk_text(content_path.read_text(encoding="utf-8")))

    hits = repository.search("MedDesk rotation", top_k=10)

    assert hits
    assert all("TRUENEX_INGESTION_METADATA" not in hit.content for hit in hits)
    assert any(hit.source_path == "docs/content.md" for hit in hits)


def test_search_excludes_recycle_bin_and_deduplicates_mirrors(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    repository = MemoryRepository(db_path)
    first_path = tmp_path / "first.md"
    mirror_path = tmp_path / "mirror.md"
    trash_path = tmp_path / "trash.md"
    shared = "AGENAS ECM internal regulatory assistant documentation."
    first_path.write_text(shared, encoding="utf-8")
    mirror_path.write_text(shared, encoding="utf-8")
    trash_path.write_text("Truenex Memory layered architecture.", encoding="utf-8")
    repository.upsert_document(first_path, "D:/Project_sw/AGENAS.md", chunk_text(shared))
    repository.upsert_document(mirror_path, "D:/GIT/AGENAS.md", chunk_text(shared))
    repository.upsert_document(
        trash_path,
        "D:/$RECYCLE.BIN/deleted-architecture.md",
        chunk_text(trash_path.read_text(encoding="utf-8")),
    )

    duplicate_hits = repository.search("AGENAS ECM documentation", top_k=10)
    architecture_hits = repository.search("layered architecture", top_k=10)

    assert len(duplicate_hits) == 1
    assert all("$RECYCLE.BIN" not in (hit.source_path or "") for hit in architecture_hits)

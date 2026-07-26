"""Tests for the missing-ledger purge, worktree exclusion, and boost change."""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.core.exclusions import should_exclude
from truenex_memory.ingestion.ledger_purge import purge_missing_ledger_entries
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.sqlite import connect


def _index_doc(repository: MemoryRepository, tmp_path: Path, relative_path: str, text: str) -> None:
    doc_path = tmp_path / Path(relative_path).name
    doc_path.write_text(text, encoding="utf-8")
    repository.upsert_document(doc_path, relative_path, chunk_text(text))


def _mark_missing(db_path: Path, relative_path: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE source_ledger SET status = 'missing' WHERE source_path_or_alias = ?",
            (relative_path,),
        )
        conn.commit()


def _counts(db_path: Path) -> dict[str, int]:
    with connect(db_path) as conn:
        return {
            "ledger_missing": conn.execute(
                "SELECT COUNT(*) FROM source_ledger WHERE status = 'missing'"
            ).fetchone()[0],
            "ledger_total": conn.execute("SELECT COUNT(*) FROM source_ledger").fetchone()[0],
            "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "fts": conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0],
        }


def _repository_with_mixed_ledger(tmp_path: Path) -> MemoryRepository:
    """One active doc, one skipped ledger row, two missing docs with chunks."""
    repository = MemoryRepository(tmp_path / "memory.db")
    _index_doc(repository, tmp_path, "docs/active.md", "active document with sharedtoken")
    _index_doc(repository, tmp_path, "docs/stale-one.md", "stale document alpha purgetoken")
    _index_doc(repository, tmp_path, ".claude/worktrees/agent-1/stale-two.md", "stale document beta purgetoken")
    db_path = tmp_path / "memory.db"
    _mark_missing(db_path, "docs/stale-one.md")
    _mark_missing(db_path, ".claude/worktrees/agent-1/stale-two.md")
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE source_ledger SET status = 'skipped' WHERE source_path_or_alias = 'docs/active.md'"
        )
        conn.execute(
            """
            INSERT INTO source_ledger (
                source_id, source_path_or_alias, source_type, status,
                created_at, updated_at
            ) VALUES ('src_skipped_extra', 'docs/skipped-extra.md', 'document', 'skipped',
                      datetime('now'), datetime('now'))
            """
        )
        conn.execute(
            "UPDATE source_ledger SET status = 'active' WHERE source_path_or_alias = 'docs/active.md'"
        )
        conn.commit()
    return repository


def test_dry_run_reports_counts_without_deleting(tmp_path: Path) -> None:
    _repository_with_mixed_ledger(tmp_path)
    db_path = tmp_path / "memory.db"
    before = _counts(db_path)

    report = purge_missing_ledger_entries(db_path, apply=False)

    assert report.dry_run is True
    assert report.db_exists is True
    assert report.missing_ledger_total == 2
    assert report.missing_ledger_selected == 2
    assert report.documents_to_delete == 2
    assert report.chunks_to_delete == 2
    assert report.ledger_deleted == 0
    assert report.documents_deleted == 0
    assert report.chunks_deleted == 0
    assert any("stale-one" in path for path in report.sample_paths)
    assert any("worktrees" in path for path in report.sample_paths)
    assert _counts(db_path) == before


def test_apply_deletes_missing_content_and_keeps_active_and_skipped(tmp_path: Path) -> None:
    _repository_with_mixed_ledger(tmp_path)
    db_path = tmp_path / "memory.db"
    before = _counts(db_path)
    assert before == {
        "ledger_missing": 2,
        "ledger_total": 4,
        "documents": 3,
        "chunks": 3,
        "fts": 3,
    }

    report = purge_missing_ledger_entries(db_path, apply=True)

    assert report.dry_run is False
    assert report.ledger_deleted == 2
    assert report.documents_deleted == 2
    assert report.chunks_deleted == 2
    after = _counts(db_path)
    assert after["ledger_missing"] == 0
    # active doc ledger row + extra skipped row are untouched
    assert after["ledger_total"] == 2
    assert after["documents"] == 1
    assert after["chunks"] == 1
    # FTS rows are removed by the chunks AFTER DELETE trigger
    assert after["fts"] == 1
    with connect(db_path) as conn:
        remaining_paths = {
            row[0] for row in conn.execute("SELECT path FROM documents").fetchall()
        }
        remaining_statuses = {
            row[0]
            for row in conn.execute("SELECT status FROM source_ledger").fetchall()
        }
    assert remaining_paths == {"docs/active.md"}
    assert remaining_statuses == {"active", "skipped"}


def test_path_filter_limits_purge_scope(tmp_path: Path) -> None:
    _repository_with_mixed_ledger(tmp_path)
    db_path = tmp_path / "memory.db"

    report = purge_missing_ledger_entries(
        db_path, apply=True, path_filters=["WORKTREES"]
    )

    assert report.missing_ledger_total == 2
    assert report.missing_ledger_selected == 1
    assert report.ledger_deleted == 1
    assert report.documents_deleted == 1
    after = _counts(db_path)
    assert after["ledger_missing"] == 1  # docs/stale-one.md survives
    with connect(db_path) as conn:
        remaining = {
            row[0]
            for row in conn.execute(
                "SELECT source_path_or_alias FROM source_ledger WHERE status = 'missing'"
            ).fetchall()
        }
        remaining_docs = {row[0] for row in conn.execute("SELECT path FROM documents").fetchall()}
    assert remaining == {"docs/stale-one.md"}
    assert remaining_docs == {"docs/active.md", "docs/stale-one.md"}


def test_document_kept_when_path_still_referenced_by_active_ledger_row(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.db")
    _index_doc(repository, tmp_path, "docs/shared.md", "shared path document purgetoken")
    db_path = tmp_path / "memory.db"
    with connect(db_path) as conn:
        # Duplicate ledger row for the same path, marked missing: the active
        # twin must protect the indexed document from the purge.
        conn.execute(
            """
            INSERT INTO source_ledger (
                source_id, source_path_or_alias, source_type, status,
                created_at, updated_at
            ) VALUES ('src_stale_twin', 'docs/shared.md', 'document', 'missing',
                      datetime('now'), datetime('now'))
            """
        )
        conn.commit()

    report = purge_missing_ledger_entries(db_path, apply=True)

    assert report.ledger_deleted == 1
    assert report.documents_kept_active_reference == 1
    assert report.documents_deleted == 0
    assert _counts(db_path)["documents"] == 1


def test_search_no_longer_returns_purged_chunks(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.db")
    _index_doc(repository, tmp_path, "docs/purge-me.md", "stale document purgetoken")
    _index_doc(repository, tmp_path, "docs/keep-me.md", "active document sharedtoken")
    db_path = tmp_path / "memory.db"

    # While the ledger row is active, the chunk is searchable.
    hits_before = repository.search("purgetoken", top_k=10)
    assert any(hit.source_path == "docs/purge-me.md" for hit in hits_before)

    # The source file disappears (ledger -> missing), then the purge runs.
    _mark_missing(db_path, "docs/purge-me.md")
    purge_missing_ledger_entries(db_path, apply=True)

    hits_after = repository.search("purgetoken", top_k=10)
    assert all(hit.source_path != "docs/purge-me.md" for hit in hits_after)
    # The active document is still searchable after the purge.
    active_hits = repository.search("sharedtoken", top_k=10)
    assert any(hit.source_path == "docs/keep-me.md" for hit in active_hits)


def test_worktrees_directory_is_excluded_from_indexing(tmp_path: Path) -> None:
    worktree_file = tmp_path / ".claude" / "worktrees" / "agent-1" / "notes.md"
    normal_file = tmp_path / "docs" / "notes.md"
    assert should_exclude(worktree_file, root=tmp_path) is True
    assert should_exclude(tmp_path / "worktrees", root=tmp_path) is True
    assert should_exclude(normal_file, root=tmp_path) is False


def test_active_twin_with_different_spelling_protects_document(tmp_path: Path) -> None:
    """Windows path spelling variants (`DOCS\\Shared.md` vs `docs/shared.md`)
    must not defeat the anti-twin guard: path matching is case- and
    separator-insensitive."""
    repository = MemoryRepository(tmp_path / "memory.db")
    _index_doc(repository, tmp_path, "docs/shared.md", "shared path document purgetoken")
    db_path = tmp_path / "memory.db"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO source_ledger (
                source_id, source_path_or_alias, source_type, status,
                created_at, updated_at
            ) VALUES ('src_stale_twin', 'DOCS\\Shared.MD', 'document', 'missing',
                      datetime('now'), datetime('now'))
            """
        )
        conn.commit()

    report = purge_missing_ledger_entries(db_path, apply=True)

    # The missing ledger row is purged, but the document is protected by
    # the active twin despite the different spelling.
    assert report.ledger_deleted == 1
    assert report.documents_kept_active_reference == 1
    assert report.documents_deleted == 0
    assert _counts(db_path)["documents"] == 1
    # Sample paths keep the ORIGINAL spelling, not the normalized key.
    assert report.sample_paths == ["DOCS\\Shared.MD"]


def test_memory_nodes_referencing_purged_documents_are_counted_and_kept(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.db")
    _index_doc(repository, tmp_path, "docs/stale-source.md", "stale source purgetoken")
    _index_doc(repository, tmp_path, "docs/active.md", "active document sharedtoken")
    db_path = tmp_path / "memory.db"
    with connect(db_path) as conn:
        stale_doc_id = conn.execute(
            "SELECT id FROM documents WHERE path = 'docs/stale-source.md'"
        ).fetchone()[0]
        conn.commit()
    repository.add_memory(
        "Curated note derived from the stale document.",
        memory_type="note",
        source_document_id=stale_doc_id,
        source_path="docs/stale-source.md",
    )
    _mark_missing(db_path, "docs/stale-source.md")

    report = purge_missing_ledger_entries(db_path, apply=True)

    assert report.memory_nodes_affected == 1
    assert report.documents_deleted == 1
    # Curated memory nodes are NEVER deleted by the purge.
    memories = repository.list_memory_nodes()
    assert len(memories) == 1
    assert memories[0].source_document_id == stale_doc_id


def test_apply_failure_rolls_back_and_reports_no_deletions(tmp_path: Path, monkeypatch) -> None:
    """A mid-apply database error must roll back the whole transaction and
    the report must not claim deletions that never happened."""
    import sqlite3 as _sqlite3

    from truenex_memory.ingestion import ledger_purge

    _repository_with_mixed_ledger(tmp_path)
    db_path = tmp_path / "memory.db"
    before = _counts(db_path)

    real_connect = ledger_purge.connect

    class _FailingConnection:
        """Delegate that raises on the first `DELETE FROM documents`."""

        def __init__(self, conn) -> None:
            self._conn = conn

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return self._conn.__exit__(exc_type, exc, tb)

        @property
        def row_factory(self):
            return self._conn.row_factory

        @row_factory.setter
        def row_factory(self, value) -> None:
            self._conn.row_factory = value

        def execute(self, sql, params=()):
            if "DELETE FROM documents" in str(sql):
                raise _sqlite3.DatabaseError("simulated mid-apply failure")
            return self._conn.execute(sql, params)

    monkeypatch.setattr(
        ledger_purge, "connect", lambda path: _FailingConnection(real_connect(path))
    )

    report = ledger_purge.purge_missing_ledger_entries(db_path, apply=True)

    assert any("simulated mid-apply failure" in warning for warning in report.warnings)
    assert report.ledger_deleted == 0
    assert report.documents_deleted == 0
    assert report.chunks_deleted == 0
    # Atomic rollback: chunks deleted before the failure are restored too.
    assert _counts(db_path) == before


def test_missing_ledger_entry_without_document_is_purged_cleanly(tmp_path: Path) -> None:
    """On the live DB some missing ledger rows have no matching document
    (620 ledger vs 616 docs): they must be counted and purged without
    errors, contributing zero documents/chunks."""
    repository = MemoryRepository(tmp_path / "memory.db")
    _index_doc(repository, tmp_path, "docs/stale.md", "stale document purgetoken")
    db_path = tmp_path / "memory.db"
    _mark_missing(db_path, "docs/stale.md")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO source_ledger (
                source_id, source_path_or_alias, source_type, status,
                created_at, updated_at
            ) VALUES ('src_ghost', 'docs/never-indexed.md', 'document', 'missing',
                      datetime('now'), datetime('now'))
            """
        )
        conn.commit()

    report = purge_missing_ledger_entries(db_path, apply=True)

    assert report.missing_ledger_selected == 2
    assert report.ledger_deleted == 2
    assert report.documents_to_delete == 1
    assert report.documents_deleted == 1
    assert report.chunks_deleted == 1
    assert not report.warnings
    assert _counts(db_path)["ledger_missing"] == 0


def test_purge_batches_more_than_500_documents(tmp_path: Path) -> None:
    """SQLite allows at most 999 variables per statement: the purge must
    batch IN (...) deletes.  1.200 documents exercise 3 batches."""
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.initialize()
    db_path = tmp_path / "memory.db"
    count = 1200
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO documents (
                id, project_id, path, filename, content_hash,
                last_indexed_at, created_at, updated_at
            ) VALUES (?, 'default', ?, ?, 'h', 'now', 'now', 'now')
            """,
            [(f"doc_{i}", f"batch/doc_{i}.md", f"doc_{i}.md") for i in range(count)],
        )
        conn.executemany(
            """
            INSERT INTO chunks (
                id, document_id, chunk_index, heading_path, content,
                content_hash, token_count, created_at, updated_at
            ) VALUES (?, ?, 0, NULL, ?, 'ch', 5, 'now', 'now')
            """,
            [(f"doc_{i}_chunk_0", f"doc_{i}", f"batchtoken content {i}") for i in range(count)],
        )
        conn.executemany(
            """
            INSERT INTO source_ledger (
                source_id, source_path_or_alias, source_type, status,
                created_at, updated_at
            ) VALUES (?, ?, 'document', 'missing', 'now', 'now')
            """,
            [(f"src_{i}", f"batch/doc_{i}.md") for i in range(count)],
        )
        conn.commit()

    report = purge_missing_ledger_entries(db_path, apply=True)

    assert report.ledger_deleted == count
    assert report.documents_deleted == count
    assert report.chunks_deleted == count
    assert not report.warnings
    assert _counts(db_path) == {
        "ledger_missing": 0,
        "ledger_total": 0,
        "documents": 0,
        "chunks": 0,
        "fts": 0,
    }

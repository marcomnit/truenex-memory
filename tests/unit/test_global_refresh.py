"""Unit tests for the global refresh module and CLI command."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from tests.unit.cli_output import plain_cli_output
from truenex_memory.cli.main import app
from truenex_memory.discovery.source_catalog import (
    CatalogEntry,
    SourceCatalog,
    source_id,
)
import truenex_memory.ingestion.global_refresh as global_refresh_module
from truenex_memory.ingestion.global_refresh import (
    RefreshReport,
    format_refresh_report,
    refresh,
)
from truenex_memory.store.sqlite import connect, initialize_schema

runner = CliRunner()


# Helpers

def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parents[1] / "unit" / f"task_work_{name}_{uuid.uuid4().hex}"
    import shutil
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _make_catalog(
    catalog_path: Path,
    entries: list[CatalogEntry],
) -> None:
    sc = SourceCatalog(entries=entries)
    sc.save(catalog_path)


def _make_project_files(project_dir: Path, files: dict[str, str]) -> None:
    """Create a project directory with given {relpath: content}."""
    project_dir.mkdir(parents=True, exist_ok=True)
    for relpath, content in files.items():
        full = project_dir / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


def _make_agent_session_files(agent_dir: Path, file_contents: dict[str, str]) -> None:
    """Create agent session directory with .jsonl files."""
    agent_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in file_contents.items():
        (agent_dir / filename).write_text(content, encoding="utf-8")


def _ledger_count(db_path: Path) -> int:
    """Count rows in source_ledger."""
    with connect(db_path) as conn:
        initialize_schema(conn)
        return conn.execute("SELECT COUNT(*) FROM source_ledger").fetchone()[0]


def _doc_count(db_path: Path) -> int:
    """Count rows in documents."""
    with connect(db_path) as conn:
        initialize_schema(conn)
        return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


class _FailingEmbedder:
    model_name = "test-failing-embedder"

    def embed(self, text: str) -> list[float]:
        raise RuntimeError("embedding backend unavailable")


class _SelectiveFailingEmbedder:
    model_name = "test-selective-failing-embedder"

    def __init__(self, needle: str) -> None:
        self.needle = needle

    def embed(self, text: str) -> list[float]:
        if self.needle in text:
            raise RuntimeError("embedding backend unavailable")
        return [0.1, 0.2, 0.3]


# Basic refresh

class TestRefreshNew:
    """Fresh catalog with valid files should index as new."""

    def test_project_root_indexes_files_as_new(self) -> None:
        wd = _workdir("refresh_new_project")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# My Project\n\nHello world.\n",
            "docs/guide.md": "# Guide\n\nThis is a guide.\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path)

        assert report.new >= 2  # at least the .md files
        assert report.indexed_records >= 2
        assert report.errors == 0
        assert report.missing == 0
        assert report.catalog_entries == 1

    def test_document_indexes_single_file(self) -> None:
        wd = _workdir("refresh_new_doc")
        doc_path = wd / "notes.md"
        doc_path.write_text("# Notes\n\nImportant notes.\n", encoding="utf-8")

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("document", str(doc_path)),
            source_type="document",
            path_or_alias=str(doc_path),
            discovered_from=["claude-projects"],
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path)

        assert report.new >= 1
        assert report.indexed_records >= 1
        assert report.errors == 0
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT path, filename FROM documents WHERE path = ?",
                (str(doc_path.resolve()),),
            ).fetchone()
        assert row is not None
        assert row["filename"] == "notes.md"

    def test_agent_root_indexes_jsonl_files(self) -> None:
        wd = _workdir("refresh_new_agent")
        agent_dir = wd / "agent_sessions"
        _make_agent_session_files(agent_dir, {
            "session1.jsonl": json.dumps({
                "type": "user", "message": {"role": "user", "content": "Hello world"}
            }) + "\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("agent_root", str(agent_dir)),
            source_type="agent_root",
            path_or_alias=str(agent_dir),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path, stability_seconds=0)

        assert report.new >= 1
        assert report.indexed_records >= 1
        assert report.errors == 0


class TestRefreshUnchanged:
    """Second run with no changes should report unchanged."""

    def test_unchanged_on_second_run(self) -> None:
        wd = _workdir("refresh_unchanged")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# My Project\n\nHello world.\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        # First run: should be new
        report1 = refresh(catalog_path, db_path)
        assert report1.new >= 1
        assert report1.unchanged == 0

        # Second run: should be unchanged
        report2 = refresh(catalog_path, db_path)
        assert report2.new == 0
        assert report2.unchanged >= 1
        assert report2.modified == 0
        assert report2.errors == 0


class TestRefreshModified:
    """Changing a file should trigger re-indexing."""

    def test_modified_file_reindexes(self) -> None:
        wd = _workdir("refresh_modified")
        project_dir = wd / "myproject"
        readme = project_dir / "README.md"
        _make_project_files(project_dir, {
            "README.md": "# Version 1\n\nHello.\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        # First run: new
        report1 = refresh(catalog_path, db_path)
        assert report1.new >= 1

        # Modify file
        time.sleep(0.02)  # ensure mtime changes
        readme.write_text("# Version 2\n\nModified.\n", encoding="utf-8")

        # Second run: should be modified
        report2 = refresh(catalog_path, db_path)
        assert report2.modified >= 1
        assert report2.new == 0


class TestRefreshDryRun:
    """Dry-run should not mutate DB or ledger."""

    def test_dry_run_does_not_mutate_db(self) -> None:
        wd = _workdir("refresh_dry_run")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# My Project\n\nHello.\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path, dry_run=True)

        # Should report new but not actually write
        assert report.new >= 1
        assert report.indexed_records == 0  # dry run does not index
        assert _doc_count(db_path) == 0
        assert _ledger_count(db_path) == 0

    def test_dry_run_then_real_run_indexes(self) -> None:
        wd = _workdir("refresh_dry_then_real")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# My Project\n\nHello.\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        # Dry run
        report_dry = refresh(catalog_path, db_path, dry_run=True)
        assert report_dry.new >= 1
        assert _doc_count(db_path) == 0

        # Real run
        report_real = refresh(catalog_path, db_path, dry_run=False)
        assert report_real.new >= 1
        assert report_real.indexed_records >= 1
        assert _doc_count(db_path) >= 1

    def test_dry_run_reads_existing_ledger_without_mutating(self) -> None:
        wd = _workdir("refresh_dry_existing")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# My Project\n\nHello.\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        refresh(catalog_path, db_path, dry_run=False)
        before_ledger_count = _ledger_count(db_path)
        before_doc_count = _doc_count(db_path)

        report = refresh(catalog_path, db_path, dry_run=True)

        assert report.unchanged >= 1
        assert report.new == 0
        assert _ledger_count(db_path) == before_ledger_count
        assert _doc_count(db_path) == before_doc_count


class TestRefreshMissingSource:
    """Catalog entry pointing to non-existent path should report missing."""

    def test_missing_source_reports_missing(self) -> None:
        wd = _workdir("refresh_missing")
        non_existent = wd / "does_not_exist"

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(non_existent)),
            source_type="project_root",
            path_or_alias=str(non_existent),
            project_name="missing_project",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path)

        assert report.missing >= 1
        assert report.new == 0
        assert report.errors == 0
        # Ledger should still record the missing entry
        assert _ledger_count(db_path) >= 1

    def test_missing_catalog_file_reports_error(self) -> None:
        wd = _workdir("refresh_no_catalog")
        catalog_path = wd / "nonexistent_sources.json"
        db_path = wd / "truenex_memory.db"

        report = refresh(catalog_path, db_path)

        assert report.errors >= 1
        assert report.catalog_entries == 0

    def test_deleted_file_under_project_root_marks_previous_record_missing(self) -> None:
        wd = _workdir("refresh_deleted_file")
        project_dir = wd / "myproject"
        readme = project_dir / "README.md"
        _make_project_files(project_dir, {
            "README.md": "# My Project\n\nHello.\n",
            "KEEP.md": "# Keep\n\nStill here.\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        first = refresh(catalog_path, db_path)
        assert first.new >= 2

        readme.unlink()
        second = refresh(catalog_path, db_path)

        assert second.missing >= 1
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT status, error_message FROM source_ledger WHERE source_path_or_alias = ?",
                (str(readme.resolve()),),
            ).fetchone()
            assert row["status"] == "missing"
            assert "no longer exists" in row["error_message"]

    def test_deleted_catalog_document_marks_file_level_record_missing(self) -> None:
        wd = _workdir("refresh_deleted_document")
        doc_path = wd / "notes.md"
        doc_path.write_text("# Notes\n\nImportant notes.\n", encoding="utf-8")

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("document", str(doc_path)),
            source_type="document",
            path_or_alias=str(doc_path),
            discovered_from=["claude-projects"],
        )
        _make_catalog(catalog_path, [entry])

        first = refresh(catalog_path, db_path)
        assert first.new >= 1

        doc_path.unlink()
        second = refresh(catalog_path, db_path)

        assert second.missing >= 2
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT status FROM source_ledger WHERE source_id = ?",
                (source_id("project_docs", str(doc_path.resolve())),),
            ).fetchone()
            assert row["status"] == "missing"


class TestRefreshServerAlias:
    """Server alias entries should be skipped without indexing."""

    def test_server_alias_is_skipped(self) -> None:
        wd = _workdir("refresh_server_alias")
        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("server_alias", "example-core"),
            source_type="server_alias",
            path_or_alias="example-core",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path)

        assert report.skipped >= 1
        assert report.new == 0
        assert report.errors == 0
        assert _doc_count(db_path) == 0


class TestRefreshJsonlStability:
    """Recently modified JSONL files should be skipped."""

    def test_recently_modified_jsonl_is_skipped(self) -> None:
        wd = _workdir("refresh_jsonl_unstable")
        agent_dir = wd / "agent_sessions"
        agent_dir.mkdir(parents=True, exist_ok=True)
        session_file = agent_dir / "session1.jsonl"
        session_file.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "Hello"}}) + "\n",
            encoding="utf-8",
        )

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("agent_root", str(agent_dir)),
            source_type="agent_root",
            path_or_alias=str(agent_dir),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        # Use a high stability threshold so the file is considered "recently modified"
        report = refresh(catalog_path, db_path, stability_seconds=9999)

        # Should be skipped due to stability
        assert report.skipped >= 1
        assert report.new == 0

    def test_stable_jsonl_is_indexed(self) -> None:
        wd = _workdir("refresh_jsonl_stable")
        agent_dir = wd / "agent_sessions"
        agent_dir.mkdir(parents=True, exist_ok=True)
        session_file = agent_dir / "session1.jsonl"
        session_file.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "Hello"}}) + "\n",
            encoding="utf-8",
        )

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("agent_root", str(agent_dir)),
            source_type="agent_root",
            path_or_alias=str(agent_dir),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        # stability_seconds=0 means always treat as stable
        report = refresh(catalog_path, db_path, stability_seconds=0)

        assert report.new >= 1
        assert report.skipped == 0

    def test_jsonl_skipped_then_stable_is_reported_new(self) -> None:
        wd = _workdir("refresh_jsonl_retry_new")
        agent_dir = wd / "agent_sessions"
        agent_dir.mkdir(parents=True, exist_ok=True)
        session_file = agent_dir / "session1.jsonl"
        session_file.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "Hello"}}) + "\n",
            encoding="utf-8",
        )

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("agent_root", str(agent_dir)),
            source_type="agent_root",
            path_or_alias=str(agent_dir),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        skipped = refresh(catalog_path, db_path, stability_seconds=9999)
        assert skipped.skipped >= 1

        indexed = refresh(catalog_path, db_path, stability_seconds=0)
        assert indexed.new >= 1
        assert indexed.modified == 0

    def test_unstable_jsonl_keeps_previous_active_ledger_entry(self) -> None:
        wd = _workdir("refresh_jsonl_keep_active")
        agent_dir = wd / "agent_sessions"
        agent_dir.mkdir(parents=True, exist_ok=True)
        session_file = agent_dir / "session1.jsonl"
        session_file.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "First"}}) + "\n",
            encoding="utf-8",
        )

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("agent_root", str(agent_dir)),
            source_type="agent_root",
            path_or_alias=str(agent_dir),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        first = refresh(catalog_path, db_path, stability_seconds=0)
        assert first.new >= 1

        session_file.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "Second"}}) + "\n",
            encoding="utf-8",
        )
        unstable = refresh(catalog_path, db_path, stability_seconds=9999)

        assert unstable.skipped >= 1
        with connect(db_path) as conn:
            # The first stable refresh produced exchange_0; the ledger stores
            # the qualified path (plain_path::exchange_0) after Bug 5 fix.
            qualified = f"{session_file.resolve()}::exchange_0"
            row = conn.execute(
                "SELECT status FROM source_ledger WHERE source_path_or_alias = ?",
                (qualified,),
            ).fetchone()
            assert row is not None, "ledger entry not found for qualified path"
            assert row["status"] == "active"

        stable = refresh(catalog_path, db_path, stability_seconds=0)
        assert stable.modified >= 1

    def test_unknown_confirmed_source_type_is_skipped(self) -> None:
        wd = _workdir("refresh_unknown_source_type")
        source_path = wd / "something"
        source_path.mkdir()
        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("unknown_type", str(source_path)),
            source_type="unknown_type",
            path_or_alias=str(source_path),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path)

        assert report.skipped == 1
        assert report.errors == 0


# Report formatting

class TestFormatReport:
    def test_format_includes_all_counts(self) -> None:
        report = RefreshReport(
            new=3, modified=1, unchanged=10, skipped=2,
            missing=1, errors=0, indexed_records=4, catalog_entries=5,
        )
        text = format_refresh_report(report)
        assert "New: 3" in text
        assert "Modified: 1" in text
        assert "Unchanged: 10" in text
        assert "Skipped: 2" in text
        assert "Missing: 1" in text
        assert "Errors: 0" in text
        assert "Indexed records: 4" in text
        assert "Catalog entries: 5" in text
        assert "Detail rows: 0" in text

    def test_format_includes_detail_summary(self) -> None:
        report = RefreshReport(
            details=[
                {
                    "source_path": "one.md",
                    "source_type": "project_docs",
                    "action": "unchanged",
                    "reason": "unchanged document skipped before hashing",
                },
                {
                    "source_path": "two.jsonl",
                    "source_type": "agent_session",
                    "action": "skipped",
                    "reason": "JSONL modified recently, not yet stable",
                },
            ]
        )

        text = format_refresh_report(report)

        assert "Detail rows: 2" in text
        assert "Detail by action: skipped=1 unchanged=1" in text
        assert "Detail by source_type: agent_session=1 project_docs=1" in text
        assert "Top reasons:" in text

    def test_to_dict_serializable(self) -> None:
        report = RefreshReport(new=1, catalog_entries=2)
        d = report.to_dict()
        assert d["new"] == 1
        assert d["catalog_entries"] == 2
        assert isinstance(d["details"], list)
        assert d["detail_summary"]["total"] == 0
        assert d["details_total"] == 0
        assert d["details_truncated"] is False

    def test_to_dict_can_limit_details(self) -> None:
        report = RefreshReport(
            details=[
                {"source_path": "one.md", "action": "unchanged"},
                {"source_path": "two.md", "action": "unchanged"},
            ]
        )

        d = report.to_dict(detail_limit=1)

        assert d["details"] == [{"source_path": "one.md", "action": "unchanged"}]
        assert d["details_total"] == 2
        assert d["detail_summary"]["by_action"] == {"unchanged": 2}
        assert d["details_truncated"] is True
        assert d["detail_limit"] == 1

    def test_to_dict_can_omit_all_details(self) -> None:
        report = RefreshReport(
            details=[{"source_path": "one.md", "action": "unchanged"}]
        )

        d = report.to_dict(detail_limit=0)

        assert d["details"] == []
        assert d["details_total"] == 1
        assert d["details_truncated"] is True
        assert d["detail_limit"] == 0


class TestRefreshPerformance:
    """Refresh should not repeat expensive per-file work for exchange records."""

    def test_unchanged_project_doc_skips_parser_on_second_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wd = _workdir("refresh_doc_parser_skip")
        doc_path = wd / "notes.md"
        doc_path.write_text("# Notes\n\nImportant content.\n", encoding="utf-8")

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("document", str(doc_path)),
            source_type="document",
            path_or_alias=str(doc_path),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        first = refresh(catalog_path, db_path)
        assert first.new == 1

        parser_calls = 0
        hash_calls = 0
        original_get_parser = global_refresh_module.get_parser
        original_parser = original_get_parser("project_docs")
        original_hash = global_refresh_module._file_content_hash

        def counting_get_parser(name: str):
            if name != "project_docs":
                return original_get_parser(name)

            def wrapped(*args, **kwargs):
                nonlocal parser_calls
                parser_calls += 1
                return original_parser(*args, **kwargs)

            return wrapped

        def counting_hash(path: Path) -> str:
            nonlocal hash_calls
            hash_calls += 1
            return original_hash(path)

        monkeypatch.setattr(global_refresh_module, "get_parser", counting_get_parser)
        monkeypatch.setattr(global_refresh_module, "_file_content_hash", counting_hash)

        second = refresh(catalog_path, db_path)

        assert second.unchanged == 1
        assert second.new == 0
        assert parser_calls == 0
        assert hash_calls == 0

    def test_hash_fast_path_refreshes_mtime_for_future_runs(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wd = _workdir("refresh_touch_mtime")
        doc_path = wd / "notes.md"
        doc_path.write_text("# Notes\n\nImportant content.\n", encoding="utf-8")

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("document", str(doc_path)),
            source_type="document",
            path_or_alias=str(doc_path),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        first = refresh(catalog_path, db_path)
        assert first.new == 1

        future = time.time() + 10
        os.utime(doc_path, (future, future))

        hash_calls = 0
        original_hash = global_refresh_module._file_content_hash

        def counting_hash(path: Path) -> str:
            nonlocal hash_calls
            hash_calls += 1
            return original_hash(path)

        monkeypatch.setattr(global_refresh_module, "_file_content_hash", counting_hash)

        second = refresh(catalog_path, db_path)
        assert second.unchanged == 1
        assert hash_calls == 1

        hash_calls = 0
        third = refresh(catalog_path, db_path)

        assert third.unchanged == 1
        assert hash_calls == 0

    def test_project_doc_parse_errors_do_not_report_no_indexable_records(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wd = _workdir("refresh_doc_parse_error")
        doc_path = wd / "notes.md"
        doc_path.write_text("# Notes\n\nImportant content.\n", encoding="utf-8")

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("document", str(doc_path)),
            source_type="document",
            path_or_alias=str(doc_path),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        original_get_parser = global_refresh_module.get_parser

        def failing_get_parser(name: str):
            if name != "project_docs":
                return original_get_parser(name)

            def fail_parser(*args, **kwargs):
                raise PermissionError("cannot read source")

            return fail_parser

        monkeypatch.setattr(global_refresh_module, "get_parser", failing_get_parser)

        report = refresh(catalog_path, db_path)

        assert report.errors == 1
        assert report.skipped == 0
        assert report.details == [{
            "source_id": entry.id,
            "source_path": str(doc_path.resolve()),
            "source_type": "project_docs",
            "action": "error",
            "error": "PermissionError: cannot read source",
        }]

    def test_jsonl_file_hash_is_cached_per_physical_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wd = _workdir("refresh_hash_cache")
        agent_dir = wd / "agent_sessions"
        session_file = agent_dir / "session.jsonl"
        _make_agent_session_files(agent_dir, {
            "session.jsonl": "\n".join([
                json.dumps({"type": "user", "message": {"role": "user", "content": "First"}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "Answer one"}}),
                json.dumps({"type": "user", "message": {"role": "user", "content": "Second"}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "Answer two"}}),
            ]) + "\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("agent_root", str(agent_dir)),
            source_type="agent_root",
            path_or_alias=str(agent_dir),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        calls: list[Path] = []
        original = global_refresh_module._file_content_hash

        def counting_hash(path: Path) -> str:
            calls.append(path)
            return original(path)

        monkeypatch.setattr(global_refresh_module, "_file_content_hash", counting_hash)

        report = refresh(catalog_path, db_path, stability_seconds=0)

        assert report.new == 2
        assert calls == [session_file.resolve()]

    def test_unchanged_jsonl_file_skips_parser_on_second_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wd = _workdir("refresh_jsonl_parser_skip")
        agent_dir = wd / "agent_sessions"
        _make_agent_session_files(agent_dir, {
            "session.jsonl": "\n".join([
                json.dumps({"type": "user", "message": {"role": "user", "content": "First"}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "Answer one"}}),
                json.dumps({"type": "user", "message": {"role": "user", "content": "Second"}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "Answer two"}}),
            ]) + "\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("agent_root", str(agent_dir)),
            source_type="agent_root",
            path_or_alias=str(agent_dir),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        first = refresh(catalog_path, db_path, stability_seconds=0)
        assert first.new == 2

        parser_calls = 0
        hash_calls = 0
        original_get_parser = global_refresh_module.get_parser
        original_parser = original_get_parser("agent_session")
        original_hash = global_refresh_module._file_content_hash

        def counting_get_parser(name: str):
            if name != "agent_session":
                return original_get_parser(name)

            def wrapped(*args, **kwargs):
                nonlocal parser_calls
                parser_calls += 1
                return original_parser(*args, **kwargs)

            return wrapped

        def counting_hash(path: Path) -> str:
            nonlocal hash_calls
            hash_calls += 1
            return original_hash(path)

        monkeypatch.setattr(global_refresh_module, "get_parser", counting_get_parser)
        monkeypatch.setattr(global_refresh_module, "_file_content_hash", counting_hash)

        second = refresh(catalog_path, db_path, stability_seconds=0)

        assert second.unchanged == 2
        assert second.new == 0
        assert parser_calls == 0
        assert hash_calls == 0

    def test_jsonl_fast_path_retries_non_active_exchange_on_second_run(
        self,
    ) -> None:
        wd = _workdir("refresh_jsonl_retry_non_active")
        agent_dir = wd / "agent_sessions"
        _make_agent_session_files(agent_dir, {
            "session.jsonl": "\n".join([
                json.dumps({"type": "user", "message": {"role": "user", "content": "First"}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "Answer one"}}),
                json.dumps({"type": "user", "message": {"role": "user", "content": "Second"}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "Answer two"}}),
            ]) + "\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("agent_root", str(agent_dir)),
            source_type="agent_root",
            path_or_alias=str(agent_dir),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        first = refresh(
            catalog_path,
            db_path,
            stability_seconds=0,
            embedder=_SelectiveFailingEmbedder("Answer two"),
        )

        assert first.new == 1
        assert first.errors == 1
        with connect(db_path) as conn:
            statuses = [
                row["status"]
                for row in conn.execute(
                    "SELECT status FROM source_ledger ORDER BY source_id"
                )
            ]
        assert sorted(statuses) == ["active", "error"]

        second = refresh(catalog_path, db_path, stability_seconds=0)

        assert second.new == 1
        assert second.errors == 0
        with connect(db_path) as conn:
            statuses = [
                row["status"]
                for row in conn.execute(
                    "SELECT status FROM source_ledger ORDER BY source_id"
                )
            ]
        assert statuses == ["active", "active"]


# Ledger entries

class TestRefreshLedger:
    """Ledger should be updated correctly during refresh."""

    def test_new_record_creates_ledger_entry(self) -> None:
        wd = _workdir("refresh_ledger_new")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {"README.md": "# Hello\n"})

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        refresh(catalog_path, db_path)

        # Should have file-level ledger entries
        count = _ledger_count(db_path)
        assert count >= 1  # at least 1 file entry

        with connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM source_ledger WHERE status = 'active'"
            ).fetchall()
            assert len(rows) >= 1

    def test_missing_entry_has_missing_status(self) -> None:
        wd = _workdir("refresh_ledger_missing")
        non_existent = wd / "does_not_exist"

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(non_existent)),
            source_type="project_root",
            path_or_alias=str(non_existent),
            project_name="missing",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        refresh(catalog_path, db_path)

        with connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM source_ledger WHERE status = 'missing'"
            ).fetchall()
            assert len(rows) >= 1

    def test_server_alias_has_skipped_status(self) -> None:
        wd = _workdir("refresh_ledger_skipped")
        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("server_alias", "my-server"),
            source_type="server_alias",
            path_or_alias="my-server",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        refresh(catalog_path, db_path)

        with connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM source_ledger WHERE source_id = ?",
                (source_id("server_alias", "my-server"),),
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["status"] == "skipped"


class TestRefreshLedgerErrors:
    """Index failures should be visible without destroying last good data."""

    def test_new_index_error_writes_error_ledger_without_document(self) -> None:
        wd = _workdir("refresh_index_error_new")
        doc_path = wd / "notes.md"
        doc_path.write_text("# Notes\n\nFresh content.\n", encoding="utf-8")

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("document", str(doc_path)),
            source_type="document",
            path_or_alias=str(doc_path),
            project_name="notes",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path, embedder=_FailingEmbedder())

        assert report.new == 0
        assert report.modified == 0
        assert report.errors == 1
        assert report.indexed_records == 0
        assert _doc_count(db_path) == 0
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT status, error_message, chunk_count FROM source_ledger"
            ).fetchone()
            assert row["status"] == "error"
            assert "embedding backend unavailable" in row["error_message"]
            assert row["chunk_count"] == 0

    def test_changed_index_error_preserves_previous_active_version(self) -> None:
        wd = _workdir("refresh_index_error_preserve")
        doc_path = wd / "notes.md"
        doc_path.write_text("# Notes\n\nprevious-good-version\n", encoding="utf-8")

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("document", str(doc_path)),
            source_type="document",
            path_or_alias=str(doc_path),
            project_name="notes",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        first = refresh(catalog_path, db_path)
        assert first.new == 1
        with connect(db_path) as conn:
            before = conn.execute(
                """
                SELECT status, content_hash, last_indexed_at, chunk_count
                FROM source_ledger
                WHERE source_path_or_alias = ?
                """,
                (str(doc_path.resolve()),),
            ).fetchone()
            before_chunk_texts = [
                row["content"] for row in conn.execute("SELECT content FROM chunks")
            ]
            assert before["status"] == "active"
            assert any(
                "previous-good-version" in content
                for content in before_chunk_texts
            )

        doc_path.write_text("# Notes\n\nbroken-new-version\n", encoding="utf-8")
        second = refresh(catalog_path, db_path, embedder=_FailingEmbedder())

        assert second.new == 0
        assert second.modified == 0
        assert second.errors == 1
        assert second.indexed_records == 0
        assert second.details[-1]["action"] == "error"
        assert second.details[-1]["preserved_previous_active"] is True
        with connect(db_path) as conn:
            after = conn.execute(
                """
                SELECT status, content_hash, last_indexed_at, chunk_count, error_message
                FROM source_ledger
                WHERE source_path_or_alias = ?
                """,
                (str(doc_path.resolve()),),
            ).fetchone()
            chunk_texts = [
                row["content"] for row in conn.execute("SELECT content FROM chunks")
            ]

        assert after["status"] == "error"
        assert "embedding backend unavailable" in after["error_message"]
        assert after["content_hash"] == before["content_hash"]
        assert after["last_indexed_at"] == before["last_indexed_at"]
        assert after["chunk_count"] == before["chunk_count"]
        assert any("previous-good-version" in content for content in chunk_texts)
        assert not any("broken-new-version" in content for content in chunk_texts)


# Empty catalog

class TestRefreshEmptyCatalog:
    """Refresh with no confirmed entries should return empty report."""

    def test_empty_catalog_returns_clean_report(self) -> None:
        wd = _workdir("refresh_empty_catalog")
        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        _make_catalog(catalog_path, [])

        report = refresh(catalog_path, db_path)

        assert report.catalog_entries == 0
        assert report.new == 0
        assert report.modified == 0
        assert report.errors == 0

    def test_catalog_with_only_candidate_status_excluded(self) -> None:
        wd = _workdir("refresh_candidate_only")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {"README.md": "# Hello\n"})

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
            confirmation_status="candidate",
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path)

        assert report.catalog_entries == 0
        assert report.new == 0


# CLI tests

class TestCliGlobalRefresh:
    """CLI tests for the 'global refresh' command."""

    def test_help_shows_refresh_command(self) -> None:
        result = runner.invoke(app, ["global", "refresh", "--help"])
        output = plain_cli_output(result.stdout)
        assert result.exit_code == 0
        assert "--home" in output
        assert "--catalog" in output
        assert "--db" in output
        assert "--dry-run" in output
        assert "--json" in output
        assert "--detail-limit" in output
        assert "--full-details" in output
        assert "--stability-seconds" in output

    def test_refresh_text_output(self) -> None:
        wd = _workdir("cli_refresh_text")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {"README.md": "# Hello\n"})

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        result = runner.invoke(app, [
            "global", "refresh",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])
        assert result.exit_code == 0
        assert "Refresh completed" in result.stdout
        assert "New:" in result.stdout

    def test_refresh_json_output(self) -> None:
        wd = _workdir("cli_refresh_json")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {"README.md": "# Hello\n"})

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        result = runner.invoke(app, [
            "global", "refresh",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "new" in payload
        assert "modified" in payload
        assert "unchanged" in payload
        assert "skipped" in payload
        assert "missing" in payload
        assert "errors" in payload
        assert "indexed_records" in payload
        assert "catalog_entries" in payload
        assert "details_total" in payload
        assert "details_truncated" in payload

    def test_refresh_json_detail_limit(self) -> None:
        wd = _workdir("cli_refresh_json_limit")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "ONE.md": "# One\n",
            "TWO.md": "# Two\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        result = runner.invoke(app, [
            "global", "refresh",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
            "--detail-limit", "1",
        ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload["details"]) == 1
        assert payload["details_total"] == 2
        assert payload["details_truncated"] is True

    def test_refresh_json_full_details_overrides_detail_limit(self) -> None:
        wd = _workdir("cli_refresh_json_full_details")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "ONE.md": "# One\n",
            "TWO.md": "# Two\n",
        })

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        result = runner.invoke(app, [
            "global", "refresh",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
            "--detail-limit", "1",
            "--full-details",
        ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload["details"]) == 2
        assert payload["details_total"] == 2
        assert payload["details_truncated"] is False

    def test_refresh_missing_catalog_reports_error(self) -> None:
        wd = _workdir("cli_refresh_no_catalog")
        catalog_path = wd / "nonexistent.json"
        db_path = wd / "truenex_memory.db"

        result = runner.invoke(app, [
            "global", "refresh",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])
        assert result.exit_code == 1
        assert "Error" in result.stdout or "Catalog file not found" in result.stdout

    def test_refresh_missing_catalog_json_output(self) -> None:
        wd = _workdir("cli_refresh_no_catalog_json")
        catalog_path = wd / "nonexistent.json"
        db_path = wd / "truenex_memory.db"

        result = runner.invoke(app, [
            "global", "refresh",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
        ])
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert "error" in payload

    def test_refresh_dry_run_text_output(self) -> None:
        wd = _workdir("cli_refresh_dryrun")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {"README.md": "# Hello\n"})

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        result = runner.invoke(app, [
            "global", "refresh",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--dry-run",
        ])
        assert result.exit_code == 0
        assert "dry-run" in result.stdout.lower()
        # DB should be untouched
        assert not db_path.exists() or _doc_count(db_path) == 0

    def test_refresh_default_paths_use_home(self) -> None:
        wd = _workdir("cli_refresh_defaults")
        home = wd / "fakehome"
        tm_dir = home / ".truenex-memory"
        tm_dir.mkdir(parents=True)

        # Create a project and write catalog under the default path
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {"README.md": "# Hello\n"})

        catalog_path = tm_dir / "sources.json"
        entry = CatalogEntry(
            id=source_id("project_root", str(project_dir)),
            source_type="project_root",
            path_or_alias=str(project_dir),
            project_name="myproject",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        db_path = tm_dir / "truenex_memory.db"

        result = runner.invoke(app, [
            "global", "refresh",
            "--home", str(home),
        ])
        assert result.exit_code == 0
        assert "Refresh completed" in result.stdout

    def test_refresh_jsonl_stability_flag_passed(self) -> None:
        wd = _workdir("cli_refresh_stability")
        agent_dir = wd / "agent_sessions"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "session1.jsonl").write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "Hello"}}) + "\n",
            encoding="utf-8",
        )

        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("agent_root", str(agent_dir)),
            source_type="agent_root",
            path_or_alias=str(agent_dir),
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        # With high stability, file should be skipped
        result = runner.invoke(app, [
            "global", "refresh",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
            "--stability-seconds", "9999",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["skipped"] >= 1

    def test_refresh_server_alias_cli(self) -> None:
        wd = _workdir("cli_refresh_server")
        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("server_alias", "example-core"),
            source_type="server_alias",
            path_or_alias="example-core",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        result = runner.invoke(app, [
            "global", "refresh",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["skipped"] >= 1
        assert payload["new"] == 0

    def test_refresh_posix_absolute_path_on_windows_is_expected_skip(self) -> None:
        wd = _workdir("remote_posix_skip")
        catalog_path = wd / "sources.json"
        db_path = wd / "truenex_memory.db"
        entry = CatalogEntry(
            id=source_id("project_root", "/opt/example-app"),
            source_type="project_root",
            path_or_alias="/opt/example-app",
            project_name="truenex",
            discovered_from=["codex-sessions"],
        )
        _make_catalog(catalog_path, [entry])

        report = refresh(catalog_path, db_path)

        if os.name == "nt":
            assert report.skipped == 1
            assert report.missing == 0
            assert "non-local" in report.details[0]["reason"]
        else:
            assert report.missing == 1

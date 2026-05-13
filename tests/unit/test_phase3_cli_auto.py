"""Tests for 'global auto run' CLI command (Phase 3.1)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import shutil
from typer.testing import CliRunner

from tests.unit.cli_output import plain_cli_output
from truenex_memory.cli.main import app
from truenex_memory.discovery.source_catalog import CatalogEntry, SourceCatalog, source_id
from truenex_memory.ingestion.global_auto_lifecycle import AUTO_MEMORY_TOMBSTONE_CONTENT
from truenex_memory.ingestion.global_auto_memory import _candidate_content
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.sqlite import connect, initialize_schema

runner = CliRunner()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"task_work_phase3_cli_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _make_catalog(catalog_path: Path, entries: list[CatalogEntry]) -> None:
    SourceCatalog(entries=entries).save(catalog_path)


def _make_project_files(project_dir: Path, files: dict[str, str]) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    for relpath, content in files.items():
        full = project_dir / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


def _doc_count(db_path: Path) -> int:
    with connect(db_path) as conn:
        initialize_schema(conn)
        return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


def _ledger_count(db_path: Path) -> int:
    with connect(db_path) as conn:
        initialize_schema(conn)
        return conn.execute("SELECT COUNT(*) FROM source_ledger").fetchone()[0]


# ---------------------------------------------------------------------------
# Help / existence
# ---------------------------------------------------------------------------


class TestAutoHelp:
    def test_global_auto_help(self) -> None:
        result = runner.invoke(app, ["global", "auto", "--help"])
        assert result.exit_code == 0
        assert "auto" in result.stdout.lower()

    def test_global_auto_run_help_shows_options(self) -> None:
        result = runner.invoke(app, ["global", "auto", "run", "--help"])
        output = plain_cli_output(result.stdout)
        assert result.exit_code == 0
        assert "--home" in output
        assert "--catalog" in output
        assert "--db" in output
        assert "--dry-run" in output
        assert "--skip-refresh" in output
        assert "--json" in output
        assert "--detail-limit" in output
        assert "--full-details" in output
        assert "--stability-seconds" in output
        assert "--auto-memory" in output
        assert "--min-confidence" in output
        assert "--auto-memory-limit" in output
        assert "source path per run" in output


# ---------------------------------------------------------------------------
# Behaviour mirrors global refresh
# ---------------------------------------------------------------------------


class TestAutoRunBehaviour:
    def test_text_output_includes_counts(self) -> None:
        wd = _workdir("auto_text")
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
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])
        assert result.exit_code == 0
        assert "Refresh completed" in result.stdout
        assert "New:" in result.stdout

    def test_json_output_has_all_keys(self) -> None:
        wd = _workdir("auto_json")
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
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        for key in ("new", "modified", "unchanged", "skipped", "missing",
                     "errors", "indexed_records", "catalog_entries"):
            assert key in payload, f"missing key {key!r}"

    def test_dry_run_does_not_mutate(self) -> None:
        wd = _workdir("auto_dryrun")
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
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--dry-run",
        ])
        assert result.exit_code == 0
        assert "dry-run" in result.stdout.lower()
        assert not db_path.exists() or _doc_count(db_path) == 0

    def test_missing_catalog_reports_error(self) -> None:
        wd = _workdir("auto_no_catalog")
        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(wd / "nope.json"),
            "--db", str(wd / "memory.db"),
        ])
        assert result.exit_code == 1
        assert "Catalog file not found" in result.stdout

    def test_missing_catalog_json_reports_error(self) -> None:
        wd = _workdir("auto_no_catalog_json")
        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(wd / "nope.json"),
            "--db", str(wd / "memory.db"),
            "--json",
        ])
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert "error" in payload

    def test_skip_refresh_requires_auto_memory(self) -> None:
        wd = _workdir("auto_skip_requires_memory")
        db_path = wd / "memory.db"
        db_path.write_bytes(b"not used")

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--db", str(db_path),
            "--skip-refresh",
            "--json",
        ])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert "--skip-refresh requires --auto-memory" in payload["error"]

    def test_skip_refresh_missing_db_does_not_create_paths(self) -> None:
        wd = _workdir("auto_skip_missing_db")
        home = wd / "home"

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--home", str(home),
            "--skip-refresh",
            "--auto-memory",
            "--json",
        ])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert "Database file not found" in payload["error"]
        assert not (home / ".truenex-memory").exists()

    def test_default_paths_use_home(self) -> None:
        wd = _workdir("auto_defaults")
        home = wd / "fakehome"
        tm_dir = home / ".truenex-memory"
        tm_dir.mkdir(parents=True)

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

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--home", str(home),
        ])
        assert result.exit_code == 0
        assert "Refresh completed" in result.stdout

    def test_stability_seconds_affects_jsonl_skipping(self) -> None:
        wd = _workdir("auto_stability")
        agent_dir = wd / "agent_sessions"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "session.jsonl").write_text(
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

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
            "--stability-seconds", "9999",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["skipped"] >= 1


class TestAutoMemoryGeneration:
    def test_auto_memory_flag_creates_unverified_nodes(self) -> None:
        wd = _workdir("auto_memory_create")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": (
                "# Decision\n\n"
                "Use SQLite for local metadata because the memory layer is local first."
            )
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--json",
        ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["auto_memory_candidates"] >= 1
        assert payload["auto_memory_created"] >= 1
        with connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT status, source_kind, created_by, confidence,
                       source_document_id, source_chunk_id, source_path, content
                FROM memory_nodes
                WHERE created_by = 'auto'
                """
            ).fetchone()
        assert row["status"] == "unverified"
        assert row["source_kind"] == "auto"
        assert row["created_by"] == "auto"
        assert row["confidence"] == 0.8
        assert row["source_document_id"]
        assert row["source_chunk_id"]
        assert row["source_path"].endswith("README.md")
        assert "TRUENEX_INGESTION_METADATA" not in row["content"]

        status_result = runner.invoke(app, [
            "global", "auto", "status",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--json",
        ])
        assert status_result.exit_code == 0
        status_payload = json.loads(status_result.stdout)
        assert status_payload["auto"]["unverified_memory_count"] >= 1

    def test_auto_memory_dry_run_reports_without_creating(self) -> None:
        wd = _workdir("auto_memory_dry")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# Notes\n\nDry run should report planned generated memory."
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)
        runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--dry-run",
            "--json",
        ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["auto_memory_candidates"] >= 1
        assert payload["auto_memory_created"] >= 1
        with connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        assert count == 0

    def test_auto_memory_skip_refresh_uses_existing_index_without_catalog(self) -> None:
        wd = _workdir("auto_memory_skip_refresh")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# Notes\n\nExisting indexed content can generate memory without refresh."
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)
        initial = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])
        assert initial.exit_code == 0
        catalog_path.unlink()

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--skip-refresh",
            "--auto-memory",
            "--dry-run",
            "--json",
        ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["refresh_skipped"] is True
        assert payload["catalog_entries"] == 0
        assert payload["auto_memory_candidates"] >= 1
        assert payload["auto_memory_created"] >= 1
        with connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        assert count == 0

    def test_auto_memory_skip_refresh_can_create_from_existing_index(self) -> None:
        wd = _workdir("auto_memory_skip_refresh_create")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# Notes\n\nExisting indexed content can create memory without refresh."
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)
        runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--db", str(db_path),
            "--skip-refresh",
            "--auto-memory",
            "--json",
        ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["refresh_skipped"] is True
        assert payload["auto_memory_created"] >= 1
        with connect(db_path) as conn:
            count = conn.execute(
                """
                SELECT COUNT(*) FROM memory_nodes
                WHERE status = 'unverified' AND source_kind = 'auto' AND created_by = 'auto'
                """
            ).fetchone()[0]
        assert count >= 1

    def test_auto_memory_skips_exact_active_duplicate(self) -> None:
        wd = _workdir("auto_memory_active_dup")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# Notes\n\nDuplicate active memory should not be overwritten."
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)
        runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])
        duplicate_content = _first_candidate_content(db_path)
        MemoryRepository(db_path).add_memory(
            duplicate_content,
            memory_type="note",
            status="active",
            created_by="user",
        )

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--json",
        ])

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert payload["auto_memory_duplicates"] >= 1
        assert payload["auto_memory_duplicate_active"] >= 1
        assert payload["auto_memory_duplicate_unverified"] == 0
        assert payload["auto_memory_duplicate_rejected"] == 0
        with connect(db_path) as conn:
            auto_count = conn.execute(
                "SELECT COUNT(*) FROM memory_nodes WHERE created_by = 'auto'"
            ).fetchone()[0]
        assert auto_count == 0

    def test_auto_memory_skips_exact_unverified_duplicate(self) -> None:
        wd = _workdir("auto_memory_unverified_dup")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# Notes\n\nDuplicate unverified memory should be skipped."
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)
        runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])
        duplicate_content = _first_candidate_content(db_path)
        MemoryRepository(db_path).add_memory(
            duplicate_content,
            memory_type="note",
            status="unverified",
            source_kind="auto",
            created_by="auto",
        )

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--json",
        ])

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert payload["auto_memory_duplicates"] >= 1
        assert payload["auto_memory_duplicate_active"] == 0
        assert payload["auto_memory_duplicate_unverified"] >= 1
        assert payload["auto_memory_duplicate_rejected"] == 0
        with connect(db_path) as conn:
            total_count = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        assert total_count == 1

    def test_auto_memory_skips_exact_obsolete_duplicate_as_reject_tombstone(self) -> None:
        wd = _workdir("auto_memory_obsolete_dup")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# Notes\n\nRejected generated memory should stay rejected."
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)
        runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])
        duplicate_content = _first_candidate_content(db_path)
        MemoryRepository(db_path).add_memory(
            duplicate_content,
            memory_type="note",
            status="obsolete",
            source_kind="auto",
            created_by="auto",
        )

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--json",
        ])

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert payload["auto_memory_duplicates"] >= 1
        assert payload["auto_memory_duplicate_active"] == 0
        assert payload["auto_memory_duplicate_unverified"] == 0
        assert payload["auto_memory_duplicate_rejected"] >= 1
        with connect(db_path) as conn:
            total_count = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
            obsolete_count = conn.execute(
                "SELECT COUNT(*) FROM memory_nodes WHERE status = 'obsolete'"
            ).fetchone()[0]
        assert total_count == 1
        assert obsolete_count == 1

    def test_auto_memory_does_not_treat_manual_obsolete_duplicate_as_tombstone(self) -> None:
        wd = _workdir("auto_memory_manual_obsolete_dup")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# Notes\n\nManual obsolete memory should not suppress auto generation."
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)
        runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])
        duplicate_content = _first_candidate_content(db_path)
        MemoryRepository(db_path).add_memory(
            duplicate_content,
            memory_type="note",
            status="obsolete",
            created_by="user",
        )

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--json",
        ])

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert payload["auto_memory_created"] >= 1
        with connect(db_path) as conn:
            auto_unverified_count = conn.execute(
                """
                SELECT COUNT(*) FROM memory_nodes
                WHERE status = 'unverified' AND source_kind = 'auto' AND created_by = 'auto'
                """
            ).fetchone()[0]
        assert auto_unverified_count == 1

    def test_auto_memory_pruned_tombstone_still_suppresses_regeneration(self) -> None:
        wd = _workdir("auto_memory_pruned_tombstone")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# Notes\n\nPruned rejected memory should stay suppressed."
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)
        runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
        ])
        duplicate_content = _first_candidate_content(db_path)
        MemoryRepository(db_path).add_memory(
            duplicate_content,
            memory_type="note",
            status="obsolete",
            source_kind="auto",
            created_by="auto",
        )
        prune_result = runner.invoke(app, [
            "global", "auto", "prune",
            "--db", str(db_path),
            "--yes",
        ])
        assert prune_result.exit_code == 0

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--json",
        ])

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert payload["auto_memory_duplicates"] >= 1
        assert payload["auto_memory_duplicate_rejected"] >= 1
        with connect(db_path) as conn:
            rows = conn.execute("SELECT status, content FROM memory_nodes").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "obsolete"
        assert rows[0]["content"] == AUTO_MEMORY_TOMBSTONE_CONTENT

    def test_auto_memory_min_confidence_skips_candidates(self) -> None:
        wd = _workdir("auto_memory_confidence")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "README.md": "# Notes\n\nConfidence threshold should control generated memory."
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--min-confidence", "0.9",
            "--json",
        ])

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert payload["auto_memory_low_confidence"] >= 1
        assert payload["auto_memory_created"] == 0
        with connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        assert count == 0

    def test_auto_memory_default_limit_can_be_lowered(self) -> None:
        wd = _workdir("auto_memory_limit")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "a.md": "# A\n\nFirst generated memory candidate has enough useful words.",
            "b.md": "# B\n\nSecond generated memory candidate also has enough useful words.",
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--auto-memory-limit", "1",
            "--json",
        ])

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert payload["auto_memory_candidates"] >= 2
        assert payload["auto_memory_created"] == 1
        assert payload["auto_memory_limit_skipped"] >= 1
        with connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        assert count == 1

    def test_auto_memory_per_source_limit_diversifies_batch(self) -> None:
        wd = _workdir("auto_memory_source_limit")
        project_dir = wd / "myproject"
        many_sections = "\n\n".join(
            f"# Section {index}\n\n"
            "This source has enough useful words to become a memory candidate."
            for index in range(1, 7)
        )
        _make_project_files(project_dir, {
            "a.md": many_sections,
            "b.md": "# B\n\nAnother document should still get represented in the batch.",
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--auto-memory-limit", "10",
            "--auto-memory-per-source-limit", "2",
            "--json",
        ])

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert payload["auto_memory_candidates"] >= 7
        assert payload["auto_memory_created"] == 3
        assert payload["auto_memory_source_limit_skipped"] >= 4
        with connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT source_path, COUNT(*) AS count
                FROM memory_nodes
                WHERE created_by = 'auto'
                GROUP BY source_path
                """
            ).fetchall()
        counts = {Path(row["source_path"]).name: row["count"] for row in rows}
        assert counts == {"a.md": 2, "b.md": 1}

    def test_auto_memory_skips_non_document_chunks(self) -> None:
        wd = _workdir("auto_memory_non_doc")
        project_dir = wd / "myproject"
        _make_project_files(project_dir, {
            "app.py": "def run():\n    return 'code chunk should not become memory'\n",
            "README.md": "# Notes\n\nDocumentation chunk should become unverified memory.",
        })
        catalog_path, db_path = _catalog_for_project(wd, project_dir)

        result = runner.invoke(app, [
            "global", "auto", "run",
            "--catalog", str(catalog_path),
            "--db", str(db_path),
            "--auto-memory",
            "--json",
        ])

        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert payload["auto_memory_non_document_skipped"] >= 1
        with connect(db_path) as conn:
            rows = conn.execute(
                "SELECT source_path FROM memory_nodes WHERE created_by = 'auto'"
            ).fetchall()
        assert rows
        assert all(row["source_path"].endswith("README.md") for row in rows)


def _catalog_for_project(wd: Path, project_dir: Path) -> tuple[Path, Path]:
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
    return catalog_path, db_path


def _first_candidate_content(db_path: Path) -> str:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT content FROM chunks ORDER BY id").fetchall()
    for row in rows:
        text = _candidate_content(row["content"])
        if text:
            return text
    raise AssertionError("expected at least one auto-memory candidate")

"""Tests for 'global auto status' CLI command (Phase 3.2)."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from tests.unit.cli_output import plain_cli_output
from truenex_memory.cli.main import app
from truenex_memory.discovery.source_catalog import CatalogEntry, SourceCatalog, source_id
from truenex_memory.ingestion.global_auto_status import (
    build_auto_status,
    format_auto_status_report,
)
from truenex_memory.ingestion.global_refresh import refresh
from truenex_memory.store.source_ledger import upsert_ledger_entry
from truenex_memory.store.sqlite import connect, initialize_schema

runner = CliRunner()


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"task_work_auto_status_{name}_{uuid.uuid4().hex}"
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


def _project_entry(project_dir: Path) -> CatalogEntry:
    return CatalogEntry(
        id=source_id("project_root", str(project_dir)),
        source_type="project_root",
        path_or_alias=str(project_dir),
        project_name="project",
        discovered_from=["codex-sessions"],
    )


def _server_alias_entry(alias: str) -> CatalogEntry:
    return CatalogEntry(
        id=source_id("server_alias", alias),
        source_type="server_alias",
        path_or_alias=alias,
        discovered_from=["codex-sessions"],
    )


def test_global_auto_help_includes_status() -> None:
    result = runner.invoke(app, ["global", "auto", "--help"])

    assert result.exit_code == 0
    assert "status" in result.stdout


def test_global_auto_status_help_shows_options() -> None:
    result = runner.invoke(app, ["global", "auto", "status", "--help"])
    output = plain_cli_output(result.stdout)

    assert result.exit_code == 0
    assert "--home" in output
    assert "--catalog" in output
    assert "--db" in output
    assert "--stability-seconds" in output
    assert "--json" in output


def test_auto_status_missing_paths_do_not_create_default_paths() -> None:
    wd = _workdir("readonly")
    home = wd / "home"

    result = runner.invoke(app, ["global", "auto", "status", "--home", str(home)])

    assert result.exit_code == 0
    assert "Auto Memory Status (Phase 3.2)" in result.stdout
    assert not (home / ".truenex-memory").exists()


def test_auto_status_json_has_auto_section() -> None:
    wd = _workdir("json")
    home = wd / "home"

    result = runner.invoke(app, ["global", "auto", "status", "--home", str(home), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["catalog"]["exists"] is False
    assert payload["database"]["exists"] is False
    assert payload["auto"]["phase"] == "3.2"
    assert payload["auto"]["ready"] is False
    assert payload["auto"]["unverified_memory_count"] == 0
    assert payload["auto"]["duplicate_skips"] == 0
    assert payload["auto"]["auto_memory_candidates"] == 0
    assert payload["auto"]["duplicate_active_skips"] == 0
    assert payload["auto"]["duplicate_unverified_skips"] == 0
    assert payload["auto"]["duplicate_rejected_skips"] == 0
    assert payload["auto"]["warnings"]


def test_auto_status_ready_after_refresh() -> None:
    wd = _workdir("ready")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n\nWorld.\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    report = build_auto_status(catalog_path, db_path)

    assert report.ready is True
    assert report.confirmed_sources == 1
    assert report.active_sources >= 1
    assert report.missing_sources == 0
    assert report.error_sources == 0
    assert report.last_auto_run_at is not None


def test_auto_status_reports_current_auto_memory_duplicate_telemetry() -> None:
    wd = _workdir("duplicate_telemetry")
    project_dir = wd / "project"
    _make_project_files(
        project_dir,
        {
            "README.md": (
                "# Decision\n\n"
                "Use SQLite for local metadata because the memory layer is local first."
            )
        },
    )
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])

    run_result = runner.invoke(
        app,
        [
            "global",
            "auto",
            "run",
            "--catalog",
            str(catalog_path),
            "--db",
            str(db_path),
            "--auto-memory",
            "--json",
        ],
    )
    assert run_result.exit_code == 0

    status_result = runner.invoke(
        app,
        [
            "global",
            "auto",
            "status",
            "--catalog",
            str(catalog_path),
            "--db",
            str(db_path),
            "--json",
        ],
    )

    assert status_result.exit_code == 0
    payload = json.loads(status_result.stdout)
    auto = payload["auto"]
    assert auto["unverified_memory_count"] >= 1
    assert auto["auto_memory_candidates"] >= 1
    assert auto["duplicate_skips"] >= 1
    assert auto["duplicate_unverified_skips"] >= 1
    assert auto["duplicate_active_skips"] == 0
    assert auto["duplicate_rejected_skips"] == 0


def test_auto_status_server_alias_skip_is_expected() -> None:
    wd = _workdir("server_alias")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir), _server_alias_entry("example-core")])
    refresh(catalog_path, db_path)

    report = build_auto_status(catalog_path, db_path)

    assert report.ready is True
    assert report.expected_skipped_sources == 1
    assert report.skipped_sources == 0
    assert report.global_status.problem_counts["skipped"] == 1


def test_auto_status_nonlocal_path_skip_is_expected() -> None:
    wd = _workdir("nonlocal_skip")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            "project_root:remote",
            "/opt/example-app",
            "project_root",
            status="skipped",
            error_message="non-local path: no local filesystem indexing",
        )

    report = build_auto_status(catalog_path, db_path)

    assert report.ready is True
    assert report.expected_skipped_sources == 1
    assert report.skipped_sources == 0


def test_auto_status_source_health_cleanup_skips_are_expected() -> None:
    wd = _workdir("health_expected")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    messages = [
        "stale ledger: no confirmed catalog source",
        "removed local source: no active local content",
        "disabled catalog source: local path not indexed",
        "no indexable records",
    ]
    with connect(db_path) as conn:
        initialize_schema(conn)
        for index, message in enumerate(messages):
            upsert_ledger_entry(
                conn,
                f"expected:{index}",
                str(wd / f"source-{index}.md"),
                "project_docs",
                status="skipped",
                error_message=message,
            )

    report = build_auto_status(catalog_path, db_path)

    assert report.ready is True
    assert report.expected_skipped_sources == len(messages)
    assert report.skipped_sources == 0


def test_auto_status_missing_and_error_sources_block_readiness() -> None:
    wd = _workdir("problems")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            "missing:1",
            str(wd / "missing.md"),
            "project_docs",
            status="missing",
            error_message="gone",
        )
        upsert_ledger_entry(
            conn,
            "error:1",
            str(wd / "bad.md"),
            "project_docs",
            status="error",
            error_message="parse failed",
        )

    report = build_auto_status(catalog_path, db_path)

    assert report.ready is False
    assert report.missing_sources == 1
    assert report.error_sources == 1
    assert any("missing" in warning for warning in report.warnings)
    assert any("errors" in warning for warning in report.warnings)


def test_auto_status_unstable_session_is_counted_as_non_expected_skip() -> None:
    wd = _workdir("unstable")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            "session:1",
            str(wd / "session.jsonl"),
            "agent_session",
            status="skipped",
            error_message="jsonl source is not yet stable",
        )

    report = build_auto_status(catalog_path, db_path)

    assert report.ready is False
    assert report.skipped_sources == 1
    assert report.actionable_skipped_sources == 1
    assert report.unstable_session_sources == 1
    assert report.transient_unstable_session_sources == 0
    assert report.stale_unstable_session_sources == 1
    assert report.warnings == [
        "1 agent session source(s) remain unstable after the stability window"
    ]
    assert report.skipped_reason_counts == [
        {
            "source_type": "agent_session",
            "reason": "jsonl source is not yet stable",
            "count": 1,
        }
    ]
    assert report.unstable_session_files == [
        {
            "path": str(wd / "session.jsonl"),
            "count": 1,
            "first_last_modified_at": None,
            "last_last_modified_at": None,
            "last_updated_at": report.unstable_session_files[0]["last_updated_at"],
            "freshness": "stale",
        }
    ]


def test_auto_status_recent_unstable_session_is_transient_and_ready() -> None:
    wd = _workdir("transient_unstable")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    session_path = wd / "session.jsonl"
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            "session:1",
            str(session_path) + "::exchange_0",
            "agent_session",
            status="skipped",
            error_message="JSONL modified recently, not yet stable",
            last_modified_at=now,
        )

    report = build_auto_status(catalog_path, db_path, stability_seconds=120)

    assert report.ready is True
    assert report.skipped_sources == 1
    assert report.actionable_skipped_sources == 0
    assert report.unstable_session_sources == 1
    assert report.transient_unstable_session_sources == 1
    assert report.stale_unstable_session_sources == 0
    assert report.unstable_session_files[0]["freshness"] == "transient"
    assert report.warnings == []


def test_auto_status_classifies_more_than_twenty_unstable_session_files() -> None:
    wd = _workdir("many_unstable")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    with connect(db_path) as conn:
        initialize_schema(conn)
        for index in range(25):
            upsert_ledger_entry(
                conn,
                f"session:{index}",
                str(wd / f"session-{index}.jsonl") + "::exchange_0",
                "agent_session",
                status="skipped",
                error_message="JSONL modified recently, not yet stable",
            )

    report = build_auto_status(catalog_path, db_path)

    assert report.ready is False
    assert report.skipped_sources == 25
    assert report.actionable_skipped_sources == 25
    assert report.unstable_session_sources == 25
    assert report.stale_unstable_session_sources == 25
    assert len(report.unstable_session_files) == 25
    assert all(item["freshness"] == "stale" for item in report.unstable_session_files)


def test_auto_status_stability_zero_marks_all_unstable_as_stale() -> None:
    wd = _workdir("stability_zero")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    session_path = wd / "session.jsonl"
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            "session:1",
            str(session_path) + "::exchange_0",
            "agent_session",
            status="skipped",
            error_message="JSONL modified recently, not yet stable",
            last_modified_at=now,
        )

    report = build_auto_status(catalog_path, db_path, stability_seconds=0)

    assert report.ready is False
    assert report.actionable_skipped_sources == 1
    assert report.transient_unstable_session_sources == 0
    assert report.stale_unstable_session_sources == 1
    assert report.unstable_session_files[0]["freshness"] == "stale"


def test_auto_status_text_output_shows_counts() -> None:
    wd = _workdir("text")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    result = runner.invoke(
        app,
        [
            "global",
            "auto",
            "status",
            "--catalog",
            str(catalog_path),
            "--db",
            str(db_path),
        ],
    )

    assert result.exit_code == 0
    assert "Auto Memory Status (Phase 3.2)" in result.stdout
    assert "ready: yes" in result.stdout
    assert "confirmed_sources:" in result.stdout
    assert "active_sources:" in result.stdout
    assert "actionable_skipped_sources:" in result.stdout
    assert "transient_unstable_session_sources:" in result.stdout
    assert "stale_unstable_session_sources:" in result.stdout
    assert "auto_memory_candidates:" in result.stdout
    assert "duplicate_active_skips:" in result.stdout
    assert "duplicate_unverified_skips:" in result.stdout
    assert "duplicate_rejected_skips:" in result.stdout
    assert "low_confidence_skips:" in result.stdout


def test_auto_status_text_output_shows_skipped_breakdown() -> None:
    wd = _workdir("text_skipped")
    project_dir = wd / "project"
    _make_project_files(project_dir, {"README.md": "# Hello\n"})
    catalog_path = wd / "sources.json"
    db_path = wd / "truenex_memory.db"
    _make_catalog(catalog_path, [_project_entry(project_dir)])
    refresh(catalog_path, db_path)

    session_path = wd / "session.jsonl"
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            "session:1",
            str(session_path) + "::exchange_0",
            "agent_session",
            status="skipped",
            error_message="JSONL modified recently, not yet stable",
        )

    result = runner.invoke(
        app,
        [
            "global",
            "auto",
            "status",
            "--catalog",
            str(catalog_path),
            "--db",
            str(db_path),
        ],
    )

    assert result.exit_code == 0
    assert "Skipped Breakdown (all ledger skipped rows):" in result.stdout
    assert "Unstable Session Files:" in result.stdout
    assert str(session_path) in result.stdout


def test_format_auto_status_report() -> None:
    wd = _workdir("format")
    report = build_auto_status(wd / "missing.json", wd / "missing.db")

    text = format_auto_status_report(report)

    assert "Auto Memory Status (Phase 3.2)" in text
    assert "Auto Readiness:" in text
    assert "Auto Counts:" in text

"""Tests for 'global auto review' CLI command."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from typer.testing import CliRunner

from tests.unit.cli_output import plain_cli_output
from truenex_memory.cli.main import app
from truenex_memory.ingestion.global_auto_review import (
    build_auto_memory_review,
    format_auto_memory_review,
)
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.sqlite import connect, initialize_schema

runner = CliRunner()


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"task_work_auto_review_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _add_auto_memory(
    db_path: Path,
    content: str,
    *,
    title: str,
    source_path: str,
) -> str:
    return MemoryRepository(db_path).add_memory(
        content,
        memory_type="note",
        title=title,
        status="unverified",
        source_kind="auto",
        source_document_id="doc_1",
        source_chunk_id="doc_1_chunk_1",
        source_path=source_path,
        created_by="auto",
        confidence=0.8,
    )


def test_global_auto_help_includes_review() -> None:
    result = runner.invoke(app, ["global", "auto", "--help"])

    assert result.exit_code == 0
    assert "review" in result.stdout


def test_auto_review_help_shows_options() -> None:
    result = runner.invoke(app, ["global", "auto", "review", "--help"])
    output = plain_cli_output(result.stdout)

    assert result.exit_code == 0
    assert "--home" in output
    assert "--db" in output
    assert "--limit" in output
    assert "--source" in output
    assert "--content-chars" in output
    assert "--json" in output


def test_auto_review_missing_db_does_not_create_default_paths() -> None:
    wd = _workdir("missing")
    home = wd / "home"

    result = runner.invoke(app, ["global", "auto", "review", "--home", str(home)])

    assert result.exit_code == 0
    assert "Auto Memory Review" in result.stdout
    assert "database not found" in result.stdout
    assert not (home / ".truenex-memory").exists()


def test_auto_review_json_lists_only_generated_unverified_auto_nodes() -> None:
    wd = _workdir("json")
    db_path = wd / "memory.db"
    auto_id = _add_auto_memory(
        db_path,
        "Generated memory content from documentation source.",
        title="Generated Note",
        source_path=str(wd / "README.md"),
    )
    repo = MemoryRepository(db_path)
    repo.add_memory(
        "Manual unverified content should not be listed.",
        status="unverified",
        created_by="user",
    )
    repo.add_memory(
        "Auto active content should not be listed.",
        status="active",
        source_kind="auto",
        created_by="auto",
    )

    result = runner.invoke(app, [
        "global", "auto", "review",
        "--db", str(db_path),
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["db_exists"] is True
    assert payload["total"] == 1
    assert payload["returned"] == 1
    assert payload["items"][0]["id"] == auto_id
    assert payload["items"][0]["status"] == "unverified"
    assert payload["items"][0]["source_kind"] == "auto"
    assert payload["items"][0]["content"] == "Generated memory content from documentation source."
    assert payload["by_source_path"][0]["count"] == 1


def test_auto_review_source_filter_and_limit() -> None:
    wd = _workdir("filter")
    db_path = wd / "memory.db"
    _add_auto_memory(
        db_path,
        "Readme content should match the source filter.",
        title="Readme",
        source_path=str(wd / "README.md"),
    )
    _add_auto_memory(
        db_path,
        "Design content should not match the readme source filter.",
        title="Design",
        source_path=str(wd / "docs" / "design.md"),
    )

    result = runner.invoke(app, [
        "global", "auto", "review",
        "--db", str(db_path),
        "--source", "readme",
        "--limit", "1",
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source_filter"] == "readme"
    assert payload["total"] == 1
    assert payload["returned"] == 1
    assert payload["items"][0]["title"] == "Readme"


def test_auto_review_source_filter_treats_like_wildcards_literally() -> None:
    wd = _workdir("wildcard_filter")
    db_path = wd / "memory.db"
    _add_auto_memory(
        db_path,
        "Percent source content should match literal percent filter.",
        title="Percent",
        source_path=str(wd / "docs" / "100%.md"),
    )
    _add_auto_memory(
        db_path,
        "Readme source content should not match literal percent filter.",
        title="Readme",
        source_path=str(wd / "README.md"),
    )

    result = runner.invoke(app, [
        "global", "auto", "review",
        "--db", str(db_path),
        "--source", "%",
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Percent"


def test_auto_review_text_output_shows_sources_and_excerpts() -> None:
    wd = _workdir("text")
    db_path = wd / "memory.db"
    _add_auto_memory(
        db_path,
        "This is a generated note with enough content to review from the terminal.",
        title="Review Note",
        source_path=str(wd / "README.md"),
    )

    result = runner.invoke(app, [
        "global", "auto", "review",
        "--db", str(db_path),
        "--content-chars", "60",
    ])

    assert result.exit_code == 0
    assert "Auto Memory Review" in result.stdout
    assert "Total unverified auto memories: 1" in result.stdout
    assert "Sources:" in result.stdout
    assert "Items:" in result.stdout
    assert "Review Note" in result.stdout
    assert "This is a generated note" in result.stdout


def test_auto_review_is_read_only() -> None:
    wd = _workdir("readonly")
    db_path = wd / "memory.db"
    _add_auto_memory(
        db_path,
        "Read-only review must not update the database file.",
        title="Read Only",
        source_path=str(wd / "README.md"),
    )
    before = db_path.stat().st_mtime_ns

    report = build_auto_memory_review(db_path)

    after = db_path.stat().st_mtime_ns
    assert report.total == 1
    assert after == before


def test_auto_review_handles_db_without_memory_table() -> None:
    wd = _workdir("no_table")
    db_path = wd / "empty.db"
    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute("DROP TABLE memory_nodes")
        conn.commit()

    report = build_auto_memory_review(db_path)
    text = format_auto_memory_review(report)

    assert report.total == 0
    assert "memory_nodes table not found" in report.warnings
    assert "memory_nodes table not found" in text

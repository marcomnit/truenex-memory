"""Tests for global auto-memory lifecycle commands."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from typer.testing import CliRunner

from truenex_memory.cli.main import app
from truenex_memory.ingestion.global_auto_lifecycle import (
    AUTO_MEMORY_TOMBSTONE_CONTENT,
    approve_auto_memory,
    promote_auto_memory,
    prune_auto_memories,
    reject_auto_memory,
)
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.sqlite import connect, initialize_schema

runner = CliRunner()


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"task_work_auto_lifecycle_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _add_auto_memory(
    db_path: Path,
    content: str,
    *,
    status: str = "unverified",
    source_path: str | None = None,
) -> str:
    return MemoryRepository(db_path).add_memory(
        content,
        memory_type="note",
        title="Generated Note",
        status=status,
        source_kind="auto",
        source_document_id="doc_1",
        source_chunk_id="chunk_1",
        source_path=source_path or "README.md",
        created_by="auto",
        confidence=0.8,
    )


def _memory_row(db_path: Path, memory_id: str):
    with connect(db_path) as conn:
        return conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (memory_id,)).fetchone()


def test_global_auto_help_includes_lifecycle_commands() -> None:
    result = runner.invoke(app, ["global", "auto", "--help"])

    assert result.exit_code == 0
    assert "approve" in result.stdout
    assert "promote" in result.stdout
    assert "reject" in result.stdout
    assert "prune" in result.stdout


def test_lifecycle_missing_db_does_not_create_default_paths() -> None:
    wd = _workdir("missing")
    home = wd / "home"

    result = runner.invoke(app, [
        "global", "auto", "approve", "mem_missing",
        "--home", str(home),
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["db_exists"] is False
    assert "database not found" in payload["warnings"]
    assert not (home / ".truenex-memory").exists()


def test_approve_promotes_only_generated_unverified_auto_memory() -> None:
    wd = _workdir("approve")
    db_path = wd / "memory.db"
    memory_id = _add_auto_memory(db_path, "Useful generated memory.")

    result = runner.invoke(app, [
        "global", "auto", "approve", memory_id,
        "--db", str(db_path),
        "--json",
    ])

    row = _memory_row(db_path, memory_id)
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["changed"] == 1
    assert payload["items"][0]["new_status"] == "active"
    assert row["status"] == "active"


def test_reject_marks_generated_unverified_auto_memory_obsolete() -> None:
    wd = _workdir("reject")
    db_path = wd / "memory.db"
    memory_id = _add_auto_memory(db_path, "Noisy generated memory.")

    result = runner.invoke(app, [
        "global", "auto", "reject", memory_id,
        "--db", str(db_path),
        "--json",
    ])

    row = _memory_row(db_path, memory_id)
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["changed"] == 1
    assert row["status"] == "obsolete"
    assert row["content"] == "Noisy generated memory."


def test_lifecycle_refuses_manual_or_non_unverified_nodes() -> None:
    wd = _workdir("guard")
    db_path = wd / "memory.db"
    repo = MemoryRepository(db_path)
    manual_id = repo.add_memory(
        "Manual unverified memory must not be auto-approved.",
        status="unverified",
        created_by="user",
    )
    active_auto_id = _add_auto_memory(db_path, "Already active auto memory.", status="active")

    manual_report = approve_auto_memory(db_path, manual_id)
    active_report = reject_auto_memory(db_path, active_auto_id)

    assert manual_report.changed == 0
    assert active_report.changed == 0
    assert manual_report.items[0].new_status is None
    assert active_report.items[0].new_status is None
    assert "not an unverified generated auto memory" in manual_report.warnings[0]
    assert _memory_row(db_path, manual_id)["status"] == "unverified"
    assert _memory_row(db_path, active_auto_id)["status"] == "active"


def test_promote_creates_curated_memory_and_obsoletes_original_atomically() -> None:
    wd = _workdir("promote")
    db_path = wd / "memory.db"
    memory_id = _add_auto_memory(
        db_path,
        "Raw session quote with a useful fact buried inside.",
        source_path="session.jsonl::exchange_1",
    )

    result = runner.invoke(app, [
        "global", "auto", "promote", memory_id,
        "--db", str(db_path),
        "--title", "QVAC MedPsy runs as a local-first desktop app",
        "--content", "QVAC MedPsy should be treated as a local-first desktop app with model files distributed locally, not as a cloud-only service.",
        "--type", "decision",
        "--json",
    ])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["changed"] == 2
    curated_id = payload["items"][0]["curated_id"]
    assert curated_id
    original = _memory_row(db_path, memory_id)
    curated = _memory_row(db_path, curated_id)
    assert original["status"] == "obsolete"
    assert original["content"] == "Raw session quote with a useful fact buried inside."
    assert curated["status"] == "active"
    assert curated["type"] == "decision"
    assert curated["title"] == "QVAC MedPsy runs as a local-first desktop app"
    assert curated["source_kind"] == "curated_auto"
    assert curated["created_by"] == "curated_auto"
    assert curated["source_path"] == "session.jsonl::exchange_1"
    assert curated["source_document_id"] == "doc_1"
    assert curated["source_chunk_id"] == "chunk_1"


def test_promote_dry_run_does_not_write() -> None:
    wd = _workdir("promote_dry")
    db_path = wd / "memory.db"
    memory_id = _add_auto_memory(db_path, "Raw generated content.")

    report = promote_auto_memory(
        db_path,
        memory_id,
        title="Curated title",
        content="Curated content.",
        dry_run=True,
    )

    assert report.dry_run is True
    assert report.matched == 1
    assert report.changed == 0
    assert report.items[0].curated_id
    assert report.items[0].new_status == "obsolete"
    assert _memory_row(db_path, memory_id)["status"] == "unverified"
    with connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM memory_nodes WHERE source_kind = 'curated_auto'"
        ).fetchone()[0] == 0


def test_promote_refuses_duplicate_active_curated_content() -> None:
    wd = _workdir("promote_duplicate")
    db_path = wd / "memory.db"
    memory_id = _add_auto_memory(db_path, "Raw generated content.")
    MemoryRepository(db_path).add_memory("Curated content.", title="Existing")

    report = promote_auto_memory(
        db_path,
        memory_id,
        title="Curated title",
        content="Curated content.",
    )

    assert report.changed == 0
    assert "active memory with same curated content already exists" in report.warnings[0]
    assert _memory_row(db_path, memory_id)["status"] == "unverified"


def test_promote_refuses_manual_or_non_unverified_nodes() -> None:
    wd = _workdir("promote_guard")
    db_path = wd / "memory.db"
    manual_id = MemoryRepository(db_path).add_memory(
        "Manual unverified memory must not be curated through auto promote.",
        status="unverified",
        created_by="user",
    )
    active_auto_id = _add_auto_memory(db_path, "Already active auto memory.", status="active")

    manual_report = promote_auto_memory(
        db_path,
        manual_id,
        title="Curated title",
        content="Curated content.",
    )
    active_report = promote_auto_memory(
        db_path,
        active_auto_id,
        title="Curated title 2",
        content="Curated content 2.",
    )

    assert manual_report.changed == 0
    assert active_report.changed == 0
    assert "not an unverified generated auto memory" in manual_report.warnings[0]
    assert "not an unverified generated auto memory" in active_report.warnings[0]
    assert _memory_row(db_path, manual_id)["status"] == "unverified"
    assert _memory_row(db_path, active_auto_id)["status"] == "active"


def test_promote_validates_required_curated_fields() -> None:
    wd = _workdir("promote_validation")
    db_path = wd / "memory.db"
    memory_id = _add_auto_memory(db_path, "Raw generated content.")

    result = runner.invoke(app, [
        "global", "auto", "promote", memory_id,
        "--db", str(db_path),
        "--title", " ",
        "--content", "Curated content.",
        "--json",
    ])

    assert result.exit_code != 0
    assert _memory_row(db_path, memory_id)["status"] == "unverified"


def test_promote_validates_required_curated_content() -> None:
    wd = _workdir("promote_empty_content")
    db_path = wd / "memory.db"
    memory_id = _add_auto_memory(db_path, "Raw generated content.")

    result = runner.invoke(app, [
        "global", "auto", "promote", memory_id,
        "--db", str(db_path),
        "--title", "Curated title",
        "--content", " ",
        "--json",
    ])

    assert result.exit_code != 0
    assert _memory_row(db_path, memory_id)["status"] == "unverified"


def test_promote_invalid_type_is_rejected_without_writing() -> None:
    wd = _workdir("promote_invalid_type")
    db_path = wd / "memory.db"
    memory_id = _add_auto_memory(db_path, "Raw generated content.")

    result = runner.invoke(app, [
        "global", "auto", "promote", memory_id,
        "--db", str(db_path),
        "--title", "Curated title",
        "--content", "Curated content.",
        "--type", "fact",
        "--json",
    ])

    assert result.exit_code != 0
    assert _memory_row(db_path, memory_id)["status"] == "unverified"
    with connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM memory_nodes WHERE source_kind = 'curated_auto'"
        ).fetchone()[0] == 0


def test_promote_reports_missing_memory_id() -> None:
    wd = _workdir("promote_missing")
    db_path = wd / "memory.db"
    MemoryRepository(db_path).initialize()

    report = promote_auto_memory(
        db_path,
        "mem_missing",
        title="Curated title",
        content="Curated content.",
    )

    assert report.changed == 0
    assert "memory node not found" in report.warnings


def test_prune_is_dry_run_by_default_and_does_not_change_content() -> None:
    wd = _workdir("prune_dry")
    db_path = wd / "memory.db"
    memory_id = _add_auto_memory(db_path, "Large rejected generated memory.", status="obsolete")

    report = prune_auto_memories(db_path)

    row = _memory_row(db_path, memory_id)
    assert report.dry_run is True
    assert report.matched == 1
    assert report.changed == 0
    assert row["content"] == "Large rejected generated memory."


def test_prune_yes_compacts_only_rejected_generated_auto_memory() -> None:
    wd = _workdir("prune_yes")
    db_path = wd / "memory.db"
    rejected_id = _add_auto_memory(
        db_path,
        "Rejected generated memory content that can be compacted.",
        status="obsolete",
        source_path="README.md",
    )
    unverified_id = _add_auto_memory(
        db_path,
        "Unverified generated memory must stay reviewable.",
        source_path="README.md",
    )
    manual_obsolete_id = MemoryRepository(db_path).add_memory(
        "Manual obsolete memory must not be compacted.",
        status="obsolete",
        created_by="user",
    )

    result = runner.invoke(app, [
        "global", "auto", "prune",
        "--db", str(db_path),
        "--yes",
        "--json",
    ])

    payload = json.loads(result.stdout)
    rejected_row = _memory_row(db_path, rejected_id)
    unverified_row = _memory_row(db_path, unverified_id)
    manual_row = _memory_row(db_path, manual_obsolete_id)
    assert result.exit_code == 0
    assert payload["dry_run"] is False
    assert payload["changed"] == 1
    assert rejected_row["status"] == "obsolete"
    assert rejected_row["content"] == AUTO_MEMORY_TOMBSTONE_CONTENT
    assert rejected_row["content_hash"]
    assert unverified_row["content"] == "Unverified generated memory must stay reviewable."
    assert manual_row["content"] == "Manual obsolete memory must not be compacted."


def test_prune_source_filter_and_limit() -> None:
    wd = _workdir("prune_filter")
    db_path = wd / "memory.db"
    readme_id = _add_auto_memory(
        db_path,
        "Readme rejected memory.",
        status="obsolete",
        source_path="README.md",
    )
    _add_auto_memory(
        db_path,
        "Design rejected memory.",
        status="obsolete",
        source_path="docs/design.md",
    )

    result = runner.invoke(app, [
        "global", "auto", "prune",
        "--db", str(db_path),
        "--source", "readme",
        "--limit", "1",
        "--yes",
        "--json",
    ])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["matched"] == 1
    assert payload["changed"] == 1
    assert _memory_row(db_path, readme_id)["content"] == AUTO_MEMORY_TOMBSTONE_CONTENT


def test_prune_source_filter_treats_like_wildcards_literally() -> None:
    wd = _workdir("prune_wildcard")
    db_path = wd / "memory.db"
    percent_id = _add_auto_memory(
        db_path,
        "Percent source rejected memory.",
        status="obsolete",
        source_path="docs/100%.md",
    )
    normal_id = _add_auto_memory(
        db_path,
        "Normal source rejected memory.",
        status="obsolete",
        source_path="README.md",
    )

    result = runner.invoke(app, [
        "global", "auto", "prune",
        "--db", str(db_path),
        "--source", "%",
        "--yes",
        "--json",
    ])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["matched"] == 1
    assert _memory_row(db_path, percent_id)["content"] == AUTO_MEMORY_TOMBSTONE_CONTENT
    assert _memory_row(db_path, normal_id)["content"] == "Normal source rejected memory."


def test_lifecycle_handles_db_without_memory_table() -> None:
    wd = _workdir("no_table")
    db_path = wd / "empty.db"
    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute("DROP TABLE memory_nodes")
        conn.commit()

    report = prune_auto_memories(db_path)

    assert report.changed == 0
    assert "memory_nodes table not found" in report.warnings

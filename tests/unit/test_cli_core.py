"""Tests for CLI core workflows."""

from contextlib import contextmanager
import json
import os
import shutil
import sqlite3
from pathlib import Path
from collections.abc import Iterator
import uuid

from typer.testing import CliRunner

from truenex_memory.cli.main import app


runner = CliRunner()


def _workdir(name: str) -> Path:
    path = Path("tests/unit") / f"task_work_{name}_{uuid.uuid4().hex}"
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


def test_init_add_search_export_workflow() -> None:
    with _cwd(_workdir("cli_workflow")):
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["add", "We use SQLite locally.", "--type", "decision"])
        assert result.exit_code == 0
        assert "mem_" in result.stdout

        result = runner.invoke(app, ["search", "SQLite locally"])
        assert result.exit_code == 0
        assert "SQLite" in result.stdout
        assert "decision" in result.stdout

        result = runner.invoke(app, ["export", "--output", "memory-export.json"])
        assert result.exit_code == 0
        assert "Exported" in result.stdout


def test_cli_lists_and_updates_memory_status() -> None:
    with _cwd(_workdir("cli_status")):
        result = runner.invoke(app, ["add", "Legacy PostgreSQL decision.", "--type", "decision"])
        assert result.exit_code == 0
        memory_id = result.stdout.strip()

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert memory_id in result.stdout
        assert "active" in result.stdout

        result = runner.invoke(app, ["status", "set", memory_id, "obsolete"])
        assert result.exit_code == 0
        assert "obsolete" in result.stdout

        result = runner.invoke(app, ["search", "PostgreSQL decision"])
        assert result.exit_code == 0
        assert "Legacy PostgreSQL" not in result.stdout

        result = runner.invoke(app, ["search", "PostgreSQL decision", "--include-inactive"])
        assert result.exit_code == 0
        assert "Legacy PostgreSQL" in result.stdout
        assert "obsolete" in result.stdout

        result = runner.invoke(app, ["list", "--status", "obsolete", "--json"])
        assert result.exit_code == 0
        assert memory_id in result.stdout
        assert '"status": "obsolete"' in result.stdout


def test_cli_search_logs_and_trace_show() -> None:
    with _cwd(_workdir("cli_trace")):
        result = runner.invoke(app, ["add", "SQLite stores retrieval traces.", "--type", "decision"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["search", "retrieval traces", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        trace_id = payload["trace_id"]
        assert trace_id.startswith("ret_")
        assert payload["results"][0]["status"] == "active"

        result = runner.invoke(app, ["logs", "--json"])
        assert result.exit_code == 0
        logs = json.loads(result.stdout)
        assert logs[0]["id"] == trace_id
        assert logs[0]["trace_id"] == trace_id
        assert logs[0]["query"] == "retrieval traces"

        result = runner.invoke(app, ["trace", "show", trace_id, "--json"])
        assert result.exit_code == 0
        trace = json.loads(result.stdout)
        assert trace["trace_id"] == trace_id
        assert trace["results"][0]["memory_type"] == "decision"
        assert trace["results"][0]["status"] == "active"


def test_cli_migrate_status_and_apply() -> None:
    with _cwd(_workdir("cli_migrate")):
        result = runner.invoke(app, ["migrate", "status", "--json"])
        assert result.exit_code == 0
        status = json.loads(result.stdout)
        assert status["current_version"] == "0"
        assert status["pending"] is True

        db_path = Path(".truenex-memory") / "truenex_memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE legacy_data (value TEXT NOT NULL)")
            conn.execute("INSERT INTO legacy_data(value) VALUES ('preserve me')")
            conn.commit()

        result = runner.invoke(app, ["migrate", "apply", "--json"])
        assert result.exit_code == 0
        applied = json.loads(result.stdout)
        assert applied["applied"] is True
        assert applied["previous_version"] == "0"
        assert applied["current_version"] == "4"
        assert Path(applied["backup_path"]).exists()

        result = runner.invoke(app, ["migrate", "apply", "--json"])
        assert result.exit_code == 0
        second = json.loads(result.stdout)
        assert second["applied"] is False
        assert second["pending"] is False


def test_doctor_privacy_reports_no_cloud() -> None:
    with _cwd(_workdir("cli_doctor")):
        result = runner.invoke(app, ["doctor", "--privacy"])

    assert result.exit_code == 0
    assert '"cloud_enabled": false' in result.stdout
    assert '"telemetry_enabled": false' in result.stdout

"""Tests for doctor vector backend reporting."""

from pathlib import Path
import uuid

from truenex_memory.diagnostics.doctor import run_doctor


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_doctor_privacy_reports_vector_backend(monkeypatch) -> None:
    monkeypatch.setenv("TRUENEX_MEMORY_VECTOR_BACKEND", "sqlite")

    report = run_doctor(_workdir("doctor_vector"), privacy=True)

    assert report["vector"]["backend"] == "sqlite"
    assert report["privacy"]["vector_backend"] == "sqlite"
    assert report["privacy"]["active_vector_backend"] == "sqlite"
    assert report["privacy"]["uploads_project_content"] is False

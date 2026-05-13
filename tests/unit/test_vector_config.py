"""Tests for optional vector backend configuration."""

from pathlib import Path
import uuid

from truenex_memory.core.config import resolve_project_config
from truenex_memory.core.memory_service import MemoryService


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_default_vector_backend_is_sqlite(monkeypatch) -> None:
    monkeypatch.delenv("TRUENEX_MEMORY_VECTOR_BACKEND", raising=False)

    config = resolve_project_config(".")

    assert config.vector_backend == "sqlite"
    assert config.qdrant_url == "http://localhost:6333"
    assert config.qdrant_collection == "truenex_memory"


def test_invalid_vector_backend_falls_back_to_sqlite(monkeypatch) -> None:
    monkeypatch.setenv("TRUENEX_MEMORY_VECTOR_BACKEND", "invalid")

    assert resolve_project_config(".").vector_backend == "sqlite"


def test_qdrant_backend_falls_back_when_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("TRUENEX_MEMORY_VECTOR_BACKEND", "qdrant")
    monkeypatch.setenv("TRUENEX_MEMORY_QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TRUENEX_MEMORY_QDRANT_COLLECTION", "test_memory")

    service = MemoryService(_workdir("qdrant_unavailable"))
    status = service.vector_status()

    assert status["backend"] == "qdrant"
    assert status["active_backend"] == "sqlite"
    assert status["available"] is False
    assert status["qdrant_collection"] == "test_memory"

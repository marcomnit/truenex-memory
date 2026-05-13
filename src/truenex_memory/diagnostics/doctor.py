"""Local diagnostics for Truenex Memory."""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.config import resolve_project_config
from truenex_memory.core.memory_service import MemoryService
from truenex_memory.store.repository import MemoryRepository


def run_doctor(project_root: Path | str = ".", *, privacy: bool = False) -> dict[str, object]:
    """Return diagnostics without contacting network services."""

    config = resolve_project_config(project_root)
    service = MemoryService(project_root)
    repo = MemoryRepository(config.db_path)
    initialized = config.db_path.exists()
    stats = repo.stats() if initialized else {"documents": 0, "chunks": 0, "memory_nodes": 0, "retrieval_logs": 0}
    result: dict[str, object] = {
        "ok": True,
        "project_root": str(config.project_root),
        "data_dir": str(config.data_dir),
        "database": str(config.db_path),
        "initialized": initialized,
        "stats": stats,
        "vector": service.vector_status(),
    }
    if privacy:
        result["privacy"] = {
            "cloud_enabled": False,
            "telemetry_enabled": False,
            "vector_backend": service.vector_status()["backend"],
            "active_vector_backend": service.vector_status()["active_backend"],
            "qdrant_url": service.vector_status()["qdrant_url"],
            "qdrant_collection": service.vector_status()["qdrant_collection"],
            "qdrant_available": service.vector_status()["available"],
            "uploads_project_content": False,
        }
    return result

"""JSON export helpers."""

from __future__ import annotations

from pathlib import Path
import json

from truenex_memory.core.memory_service import MemoryService


def export_memory(output: Path, *, project_root: Path | str = ".") -> Path:
    """Write a local memory export JSON file."""

    service = MemoryService(project_root)
    service.init_project()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(service.repository.export_data(), indent=2, sort_keys=True), encoding="utf-8")
    return output

"""JSON import helpers."""

from __future__ import annotations

from pathlib import Path
import json

from truenex_memory.core.memory_service import MemoryService


def import_memory(input_path: Path, *, project_root: Path | str = ".") -> None:
    """Import a local memory export JSON file."""

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("memory export must be a JSON object")
    service = MemoryService(project_root)
    service.init_project()
    service.repository.import_data(payload)

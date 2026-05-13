"""Test JSON export/import helpers."""

import shutil
from pathlib import Path

import pytest

from truenex_memory.export import SCHEMA_VERSION, export_json, exports_equivalent, import_json, import_records


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task3_work") / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def test_export_and_import_json_round_trip() -> None:
    path = _workdir("export_round_trip") / "memory.json"
    records = [{"id": "one", "text": "hello"}]

    payload = export_json(records, path, metadata={"source": "test"})
    imported = import_json(path)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert imported["metadata"] == {"source": "test"}
    assert import_records(path) == records


def test_import_json_rejects_invalid_schema() -> None:
    path = _workdir("export_invalid") / "bad.json"
    path.write_text('{"schema_version": 999, "records": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported schema_version"):
        import_json(path)


def test_exports_equivalent_ignores_row_and_key_order() -> None:
    first = {
        "memory_export_version": "1",
        "project_id": "default",
        "memory_nodes": [
            {"id": "b", "title": "Beta", "status": "active"},
            {"status": "obsolete", "title": "Alpha", "id": "a"},
        ],
    }
    second = {
        "project_id": "default",
        "memory_export_version": "1",
        "memory_nodes": [
            {"id": "a", "status": "obsolete", "title": "Alpha"},
            {"title": "Beta", "id": "b", "status": "active"},
        ],
    }
    changed = {
        "project_id": "default",
        "memory_export_version": "1",
        "memory_nodes": [
            {"id": "a", "status": "active", "title": "Alpha"},
            {"title": "Beta", "id": "b", "status": "active"},
        ],
    }

    assert exports_equivalent(first, second)
    assert not exports_equivalent(first, changed)

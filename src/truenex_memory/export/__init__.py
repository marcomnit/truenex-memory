"""JSON export and import helpers for local memory payloads."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1


def build_export_payload(
    records: Iterable[Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable JSON payload for memory records."""

    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "metadata": dict(metadata or {}),
        "records": [dict(record) for record in records],
    }


def export_json(
    records: Iterable[Mapping[str, Any]],
    destination: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write records to a local JSON export file and return the payload."""

    payload = build_export_payload(records, metadata=metadata)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def import_json(source: str | Path) -> dict[str, Any]:
    """Read and validate a local JSON export file."""

    path = Path(source)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("export payload must be a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {payload.get('schema_version')!r}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("export payload must contain a records list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"record {index} must be a JSON object")
    payload.setdefault("metadata", {})
    return payload


def import_records(source: str | Path) -> list[dict[str, Any]]:
    """Read records from a local JSON export file."""

    return list(import_json(source)["records"])


from truenex_memory.export.fingerprint import canonicalize_export, export_fingerprint, exports_equivalent


__all__ = [
    "SCHEMA_VERSION",
    "build_export_payload",
    "canonicalize_export",
    "export_fingerprint",
    "export_json",
    "exports_equivalent",
    "import_json",
    "import_records",
]

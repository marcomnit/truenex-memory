"""Deterministic data fingerprint for export/import equivalence checks.

Produces stable hashes and comparisons that ignore volatile row ordering
so two exports of the same logical data always compare equal.
"""

from __future__ import annotations

import hashlib
import json

_EXPORT_TABLES = ("documents", "chunks", "memory_nodes", "edges", "retrieval_logs", "schema_migrations")


def canonicalize_export(payload: dict[str, object]) -> dict[str, object]:
    """Return a canonicalized copy of *payload* with stable ordering.

    Rows within each data table are sorted by the ``id`` column.
    Keys within each row dict are sorted alphabetically.
    ``memory_export_version`` and ``project_id`` are preserved as-is.
    """
    result: dict[str, object] = {
        "memory_export_version": payload.get("memory_export_version"),
        "project_id": payload.get("project_id"),
    }
    for table in _EXPORT_TABLES:
        rows = payload.get(table, [])
        if not isinstance(rows, list):
            result[table] = rows
            continue
        sorted_rows = sorted(
            (_canonical_row(r) for r in rows if isinstance(r, dict)),
            key=lambda r: str(r.get("id", "")),
        )
        result[table] = sorted_rows
    return result


def _canonical_row(row: dict[str, object]) -> dict[str, object]:
    """Return a copy of *row* with keys sorted alphabetically."""
    return {k: row[k] for k in sorted(row)}


def export_fingerprint(payload: dict[str, object]) -> str:
    """SHA-256 hex fingerprint of a canonicalized export payload."""
    canonical = canonicalize_export(payload)
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def exports_equivalent(a: dict[str, object], b: dict[str, object]) -> bool:
    """Return ``True`` when two export payloads contain the same data.

    Volatile ordering (row order, dict key order) is ignored.
    """
    return export_fingerprint(a) == export_fingerprint(b)

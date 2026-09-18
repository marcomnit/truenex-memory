"""Disabling already-confirmed catalog entries by path pattern.

Complements `test_ledger_purge.py::purge_by_path_pattern`. An exclusion
added to discovery only stops NEW candidates: an entry confirmed before
the exclusion existed stays confirmed, and `global refresh` re-indexes
confirmed entries directly by their catalog path — it never re-runs
discovery. Found on a real archive (2026-09-18): purging the database
alone was not enough, because the confirmed catalog entries for the
purged files were still there and kept getting re-indexed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from truenex_memory.discovery.source_catalog import disable_entries_by_path_pattern


def _write_catalog(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": "1", "entries": entries}), encoding="utf-8")


def _entry(path_or_alias: str, *, status: str = "confirmed") -> dict:
    return {
        "id": f"document:{path_or_alias.lower()}",
        "source_type": "document",
        "path_or_alias": path_or_alias,
        "confirmation_status": status,
    }


def test_requires_at_least_one_non_empty_filter(tmp_path: Path) -> None:
    catalog_path = tmp_path / "sources.json"
    _write_catalog(catalog_path, [_entry(r"C:\x\tool-results\a.txt")])

    with pytest.raises(ValueError):
        disable_entries_by_path_pattern(catalog_path, patterns=[])
    with pytest.raises(ValueError):
        disable_entries_by_path_pattern(catalog_path, patterns=["   "])


def test_dry_run_reports_matches_without_writing(tmp_path: Path) -> None:
    catalog_path = tmp_path / "sources.json"
    entries = [
        _entry(r"C:\Users\x\.claude\projects\slug\s1\tool-results\a.txt"),
        _entry(r"C:\Users\x\project\README.md"),
    ]
    _write_catalog(catalog_path, entries)
    before = catalog_path.read_text(encoding="utf-8")

    report = disable_entries_by_path_pattern(catalog_path, patterns=["tool-results"])

    assert report.dry_run is True
    assert report.matched == 1
    assert report.disabled == 0
    assert catalog_path.read_text(encoding="utf-8") == before, "un dry-run non deve scrivere niente"


def test_apply_disables_only_matched_entries_and_backs_up_first(tmp_path: Path) -> None:
    catalog_path = tmp_path / "sources.json"
    entries = [
        _entry(r"C:\Users\x\.claude\projects\slug\s1\tool-results\a.txt"),
        _entry(r"C:\Users\x\project\README.md"),
    ]
    _write_catalog(catalog_path, entries)

    report = disable_entries_by_path_pattern(catalog_path, patterns=["tool-results"], apply=True)

    assert report.disabled == 1
    assert report.backup_path is not None
    backup = json.loads(Path(report.backup_path).read_text(encoding="utf-8"))
    assert backup["entries"][0]["confirmation_status"] == "confirmed", "il backup e' pre-modifica"

    after = json.loads(catalog_path.read_text(encoding="utf-8"))
    by_path = {e["path_or_alias"]: e["confirmation_status"] for e in after["entries"]}
    assert by_path[r"C:\Users\x\.claude\projects\slug\s1\tool-results\a.txt"] == "disabled"
    assert by_path[r"C:\Users\x\project\README.md"] == "confirmed"


def test_already_disabled_entries_are_not_double_counted(tmp_path: Path) -> None:
    catalog_path = tmp_path / "sources.json"
    _write_catalog(
        catalog_path,
        [_entry(r"C:\x\tool-results\a.txt", status="disabled")],
    )

    report = disable_entries_by_path_pattern(catalog_path, patterns=["tool-results"], apply=True)

    assert report.already_disabled == 1
    assert report.disabled == 0, "non c'era niente da cambiare"
    assert report.backup_path is None, "nessuna scrittura, nessun backup necessario"


def test_apply_leaves_no_leftover_temp_file(tmp_path: Path) -> None:
    """La scrittura e' write-then-rename: nessun `.tmp` deve sopravvivere
    a un apply riuscito."""

    catalog_path = tmp_path / "sources.json"
    _write_catalog(catalog_path, [_entry(r"C:\x\tool-results\a.txt")])

    disable_entries_by_path_pattern(catalog_path, patterns=["tool-results"], apply=True)

    assert not catalog_path.with_suffix(f"{catalog_path.suffix}.tmp").exists()


def test_missing_catalog_is_reported_not_raised(tmp_path: Path) -> None:
    report = disable_entries_by_path_pattern(tmp_path / "missing.json", patterns=["tool-results"])

    assert report.catalog_exists is False
    assert report.warnings

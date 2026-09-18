"""Reindex reattivo del corpus globale.

Stesso spirito dei test di `graph.refresh`: non e' un demone da avviare, e'
un controllo a basso costo dentro le porte che gia' leggono lo stato
globale. Qui si verifica il contratto — quando dichiara stale, quando si
astiene, e che rispetta l'interruttore — non l'avvio di un vero
sottoprocesso, che appartiene ai test di integrazione del CLI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from truenex_memory.ingestion.reactive_refresh import ensure_global_fresh


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def test_fresh_index_needs_no_refresh(tmp_path: Path) -> None:
    result = ensure_global_fresh(
        catalog_path=tmp_path / "sources.json",
        db_path=tmp_path / "truenex_memory.db",
        last_indexed_at=_iso(60),
    )

    assert result["stale"] is False
    assert result["refresh"] == "non necessario"


def test_missing_timestamp_counts_as_stale(tmp_path: Path) -> None:
    result = ensure_global_fresh(
        catalog_path=tmp_path / "sources.json",
        db_path=tmp_path / "truenex_memory.db",
        last_indexed_at=None,
    )

    assert result["stale"] is True


def test_stale_without_catalog_is_not_applicable(tmp_path: Path) -> None:
    result = ensure_global_fresh(
        catalog_path=tmp_path / "sources.json",
        db_path=tmp_path / "truenex_memory.db",
        last_indexed_at=_iso(7 * 24 * 60 * 60),
    )

    assert result["refresh"] == "non applicabile"


def test_stale_with_switch_off_is_reported_as_disabled(tmp_path: Path) -> None:
    catalog_path = tmp_path / "sources.json"
    catalog_path.write_text("{}", encoding="utf-8")

    result = ensure_global_fresh(
        catalog_path=catalog_path,
        db_path=tmp_path / "truenex_memory.db",
        last_indexed_at=_iso(7 * 24 * 60 * 60),
        allow_refresh=False,
    )

    assert result["refresh"] == "disattivata"
    assert "truenex-mem global refresh" in result["hint"]


def test_a_lock_already_held_is_reported_as_in_progress(tmp_path: Path) -> None:
    catalog_path = tmp_path / "sources.json"
    catalog_path.write_text("{}", encoding="utf-8")
    db_path = tmp_path / "truenex_memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (db_path.parent / f"{db_path.stem}.refreshing").write_text("12345", encoding="utf-8")

    result = ensure_global_fresh(
        catalog_path=catalog_path,
        db_path=db_path,
        last_indexed_at=_iso(7 * 24 * 60 * 60),
        allow_refresh=True,
    )

    assert result["refresh"] == "in corso"

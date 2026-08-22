"""Nessun archivio viene aggiornato in silenzio.

Perche' esiste. `initialize_schema` gira a OGNI apertura del database — una
ricerca, un handshake MCP, qualunque comando — e applica le migrazioni. Sono
additive, quindi il rischio di perdere dati e' basso; ma «basso» non e' cio' che
si promette a chi ha un archivio da 3,47 GB, e chi aggiorna il pacchetto si
trovava lo schema cambiato senza un punto di ripristino.

Prendere un backup lì non era un'alternativa praticabile: su quell'archivio
sarebbe una copia di secondi dentro un handshake, che il client vedrebbe come un
blocco. Quindi si rifiuta e si manda al comando esplicito, dove il backup si fa
e l'attesa e' attesa.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from truenex_memory.release.version import DB_SCHEMA_VERSION
from truenex_memory.store.sqlite import (
    SchemaUpgradeRequired,
    connect,
    initialize_schema,
)


def _archivio(percorso: Path, versione: str, *, con_contenuto: bool) -> Path:
    """Un database allo schema *versione*, con o senza dati dentro."""

    conn = sqlite3.connect(percorso)
    conn.executescript(
        "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT);"
        f"INSERT INTO schema_migrations VALUES ('{versione}', '2026-01-01');"
        "CREATE TABLE documents (id TEXT PRIMARY KEY, path TEXT);"
    )
    if con_contenuto:
        conn.execute("INSERT INTO documents VALUES ('a', 'x.md')")
    conn.commit()
    conn.close()
    return percorso


def test_an_older_populated_store_is_refused(tmp_path: Path) -> None:
    """Il caso che conta: dati dentro e schema indietro."""

    db = _archivio(tmp_path / "vecchio.db", "7", con_contenuto=True)

    conn = connect(db)
    try:
        with pytest.raises(SchemaUpgradeRequired) as errore:
            initialize_schema(conn)
    finally:
        conn.close()

    messaggio = str(errore.value)
    assert "truenex-mem upgrade" in messaggio, "il rifiuto deve dire cosa fare"
    assert "7" in messaggio and DB_SCHEMA_VERSION in messaggio


def test_a_fresh_store_is_created_without_friction(tmp_path: Path) -> None:
    """Un archivio nuovo non ha niente da proteggere.

    Se il guardiano scattasse anche qui, un'installazione nuova non partirebbe —
    una protezione che impedisce il primo avvio protegge da niente.
    """

    conn = connect(tmp_path / "nuovo.db")
    try:
        initialize_schema(conn)
        versione = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY CAST(version AS INTEGER) DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()

    assert versione == DB_SCHEMA_VERSION


def test_an_older_but_empty_store_is_upgraded(tmp_path: Path) -> None:
    """Senza dati non c'e' niente da perdere: bloccare sarebbe zelo inutile.

    E' il caso di chi ha inizializzato e non ha ancora indicizzato: rifiutare lo
    costringerebbe a un comando in piu' per proteggere un file vuoto.
    """

    db = _archivio(tmp_path / "vuoto.db", "7", con_contenuto=False)

    conn = connect(db)
    try:
        initialize_schema(conn)  # non deve sollevare
    finally:
        conn.close()


def test_a_newer_store_is_not_blocked(tmp_path: Path, caplog) -> None:
    """Il caso opposto non si rifiuta, si registra.

    Le migrazioni sono additive, quindi una versione precedente del codice riesce
    a leggere e scrivere un archivio piu' nuovo. Rifiutare lascerebbe a piedi chi
    ha due installazioni — e un avviso su cui non si puo' agire non deve fermare
    il lavoro.
    """

    futura = str(int(DB_SCHEMA_VERSION) + 3)
    db = _archivio(tmp_path / "futuro.db", futura, con_contenuto=True)

    conn = connect(db)
    try:
        with caplog.at_level("WARNING"):
            initialize_schema(conn)
    finally:
        conn.close()

    assert any(futura in r.getMessage() for r in caplog.records), (
        "l'avviso deve nominare la versione trovata"
    )


def test_the_migration_command_is_the_authorised_door(tmp_path: Path) -> None:
    """Il guardiano vive dentro la funzione che la migrazione deve chiamare.

    Senza un'autorizzazione esplicita, `migrate` veniva rifiutato da se' stesso:
    il comando che indichiamo per aggiornare non poteva aggiornare. Difetto
    trovato provandolo, non ragionandoci.
    """

    from truenex_memory.core.migration import migrate_apply

    db = _archivio(tmp_path / "vecchio.db", "7", con_contenuto=True)

    esito = migrate_apply(db, tmp_path / "backups")

    assert esito["applied"] is True
    assert esito["previous_version"] == "7"
    assert esito["current_version"] == DB_SCHEMA_VERSION
    assert esito["backup_path"] and Path(esito["backup_path"]).exists(), (
        "il backup e' la ragione per cui questa e' la porta autorizzata"
    )


def test_the_backup_is_taken_before_the_change(tmp_path: Path) -> None:
    """Un backup preso dopo non e' un backup.

    Si verifica sul contenuto: la copia deve essere allo schema vecchio.
    """

    from truenex_memory.core.migration import migrate_apply

    db = _archivio(tmp_path / "vecchio.db", "7", con_contenuto=True)
    esito = migrate_apply(db, tmp_path / "backups")

    copia = sqlite3.connect(esito["backup_path"])
    try:
        versione_copia = copia.execute(
            "SELECT version FROM schema_migrations ORDER BY CAST(version AS INTEGER) DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        copia.close()

    assert versione_copia == "7", "la copia deve precedere la migrazione"


def test_the_escape_hatch_exists_for_automation(tmp_path: Path, monkeypatch) -> None:
    """Serve alle prove automatiche e a chi sa cosa sta facendo.

    Senza, ogni suite che riproduce un archivio vecchio dovrebbe passare dal
    comando di migrazione, e un guardiano che non si puo' scavalcare in modo
    dichiarato viene scavalcato in modo non dichiarato.
    """

    monkeypatch.setenv("TRUENEX_ALLOW_AUTO_MIGRATE", "1")
    db = _archivio(tmp_path / "vecchio.db", "7", con_contenuto=True)

    conn = connect(db)
    try:
        initialize_schema(conn)  # non deve sollevare
    finally:
        conn.close()


def test_the_backup_lands_next_to_the_database_it_protects(tmp_path: Path) -> None:
    """Trovato provandolo: la copia finiva nella cartella del progetto corrente.

    Con `--db` che punta all'archivio globale, il backup atterrava dentro un
    progetto qualunque — lontano da cio' che protegge, dove nessuno lo
    cercherebbe il giorno in cui serve.
    """

    from truenex_memory.core.migration import migrate_apply

    dati = tmp_path / "altrove" / ".truenex-memory"
    dati.mkdir(parents=True)
    db = _archivio(dati / "truenex_memory.db", "7", con_contenuto=True)

    esito = migrate_apply(db, db.parent / "backups")

    copia = Path(esito["backup_path"])
    assert copia.parent == db.parent / "backups"
    assert copia.exists()

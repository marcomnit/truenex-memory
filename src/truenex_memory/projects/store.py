"""Dove si conserva l'attribuzione di una sorgente al suo progetto.

Perche' si conserva invece di ricalcolarla. Attribuire tutte le 17.053 sorgenti
di questo archivio costa 12,8 secondi di letture sul filesystem: risalire le
cartelle, cercare `.git` e i marcatori. E' un costo da pagare una volta, quando
si indicizza, non a ogni chiamata dell'interfaccia.

Perche' una tabella nuova e non colonne nuove in `source_ledger`. Aggiungere
colonne a una tabella esistente richiede una migrazione dello schema, e su questo
archivio da 3,5 GB una migrazione va fatta con un backup e su richiesta — e'
esattamente cio' che `guard_schema_version` protegge. Una tabella a parte e'
additiva e opzionale: chi legge e non la trova sa che la classificazione non e'
ancora stata fatta, e lo dichiara invece di indovinare. Nessun archivio esistente
smette di funzionare.

La versione della classificazione viaggia con ogni riga. Le regole
cambieranno — e' gia' successo due volte oggi, da 58 nomi a 209 e poi a 54 — e
una riga scritta con regole vecchie non deve essere indistinguibile da una
scritta con quelle nuove.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from truenex_memory.projects.identity import Attribuzione

#: Cambia quando cambiano le REGOLE di attribuzione, non quando cambia questo
#: file. Una riga con una versione precedente e' da ricalcolare, e si vede.
VERSIONE_REGOLE = 1

TABELLA = "source_attribution"


@dataclass(frozen=True)
class Riga:
    source_id: str
    attribuzione: Attribuzione


def assicura_tabella(conn: sqlite3.Connection) -> None:
    """Crea la tabella se non c'e'. Idempotente, e non tocca nient'altro."""

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABELLA} (
            source_id     TEXT PRIMARY KEY,
            kind          TEXT NOT NULL,
            project_id    TEXT,
            root          TEXT,
            display_name  TEXT,
            discovery     TEXT NOT NULL,
            reason        TEXT NOT NULL,
            rules_version INTEGER NOT NULL,
            computed_at   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABELLA}_project"
        f" ON {TABELLA} (project_id)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABELLA}_kind ON {TABELLA} (kind)"
    )


def tabella_esiste(conn: sqlite3.Connection) -> bool:
    riga = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABELLA,)
    ).fetchone()
    return riga is not None


def scrivi(conn: sqlite3.Connection, righe: list[Riga], *, adesso: str | None = None) -> int:
    """Scrive le attribuzioni, sostituendo quelle esistenti per gli stessi id."""

    assicura_tabella(conn)
    quando = adesso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.executemany(
        f"""
        INSERT INTO {TABELLA}
            (source_id, kind, project_id, root, display_name, discovery, reason,
             rules_version, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            kind=excluded.kind, project_id=excluded.project_id, root=excluded.root,
            display_name=excluded.display_name, discovery=excluded.discovery,
            reason=excluded.reason, rules_version=excluded.rules_version,
            computed_at=excluded.computed_at
        """,
        [
            (
                r.source_id, r.attribuzione.kind, r.attribuzione.project_id,
                r.attribuzione.root, r.attribuzione.display_name,
                r.attribuzione.discovery, r.attribuzione.reason,
                VERSIONE_REGOLE, quando,
            )
            for r in righe
        ],
    )
    return len(righe)


def leggi_tutte(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    """Le attribuzioni per identificatore di sorgente.

    Un dizionario vuoto quando la tabella non c'e': l'assenza si rappresenta con
    l'assenza, non con un errore. Chi legge deve poter dire «non ancora
    classificato» invece di fallire.
    """

    if not tabella_esiste(conn):
        return {}
    conn.row_factory = sqlite3.Row
    return {
        r["source_id"]: {
            "kind": r["kind"],
            "project_id": r["project_id"],
            "root": r["root"],
            "display_name": r["display_name"],
            "discovery": r["discovery"],
            "reason": r["reason"],
            "rules_version": r["rules_version"],
            "computed_at": r["computed_at"],
            # Vero quando la riga e' stata scritta con regole precedenti: il
            # valore c'e' ma non e' quello che il codice di oggi produrrebbe.
            "stale": r["rules_version"] != VERSIONE_REGOLE,
        }
        for r in conn.execute(f"SELECT * FROM {TABELLA}")
    }


def riepilogo(conn: sqlite3.Connection) -> dict[str, object]:
    """Quante righe, di che specie, con quali regole."""

    if not tabella_esiste(conn):
        return {"classified": 0, "table": False}
    per_specie = {
        k: n for k, n in conn.execute(f"SELECT kind, COUNT(*) FROM {TABELLA} GROUP BY kind")
    }
    vecchie = conn.execute(
        f"SELECT COUNT(*) FROM {TABELLA} WHERE rules_version <> ?", (VERSIONE_REGOLE,)
    ).fetchone()[0]
    progetti = conn.execute(
        f"SELECT COUNT(DISTINCT project_id) FROM {TABELLA} WHERE kind = 'project'"
    ).fetchone()[0]
    return {
        "table": True,
        "classified": sum(per_specie.values()),
        "by_kind": per_specie,
        "projects": progetti,
        "with_old_rules": vecchie,
        "rules_version": VERSIONE_REGOLE,
    }

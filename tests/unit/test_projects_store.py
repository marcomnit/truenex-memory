"""Dove si conserva l'attribuzione, e cosa succede quando non c'e'.

Perche' una tabella nuova invece di colonne in `source_ledger`. Aggiungere
colonne richiede una migrazione dello schema, e su un archivio da 3,5 GB una
migrazione va fatta su richiesta e con un backup — e' cio' che
`guard_schema_version` protegge. Una tabella additiva e opzionale non blocca
nessun archivio esistente: chi legge e non la trova sa che la classificazione non
e' stata fatta, e lo dichiara invece di indovinare.
"""

from __future__ import annotations

from pathlib import Path

from truenex_memory.projects.identity import Attribuzione
from truenex_memory.projects.store import (
    TABELLA,
    VERSIONE_REGOLE,
    Riga,
    assicura_tabella,
    leggi_tutte,
    riepilogo,
    scrivi,
    tabella_esiste,
)
from truenex_memory.store.sqlite import connect, initialize_schema


def _archivio(tmp_path: Path):
    db = tmp_path / ".truenex-memory" / "truenex_memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    initialize_schema(conn)
    return conn


def _attr(nome: str | None, kind: str = "project", discovery: str = "vcs") -> Attribuzione:
    return Attribuzione(
        kind=kind,
        project_id=f"id-{nome}" if nome else None,
        root=f"D:/p/{nome}" if nome else None,
        display_name=nome,
        discovery=discovery,
        reason="contiene .git" if kind == "project" else "cartella dati di un client",
    )


def test_un_archivio_senza_tabella_non_e_un_errore(tmp_path: Path) -> None:
    """L'assenza si rappresenta con l'assenza.

    Se leggere una classificazione mancante alzasse un errore, ogni archivio
    esistente si romperebbe al primo aggiornamento del codice.
    """

    conn = _archivio(tmp_path)
    assert tabella_esiste(conn) is False
    assert leggi_tutte(conn) == {}
    assert riepilogo(conn) == {"classified": 0, "table": False}


def test_scrive_e_rilegge(tmp_path: Path) -> None:
    conn = _archivio(tmp_path)
    scrivi(conn, [Riga("s1", _attr("App")), Riga("s2", _attr(None, "agent_data", "agent_dir"))])

    letto = leggi_tutte(conn)
    assert set(letto) == {"s1", "s2"}
    assert letto["s1"]["display_name"] == "App"
    assert letto["s1"]["discovery"] == "vcs"
    assert letto["s2"]["kind"] == "agent_data"
    assert letto["s2"]["display_name"] is None
    # La ragione viaggia con la riga: senza, un'attribuzione e' un verdetto senza
    # motivazione e nessuno puo' contestarla.
    assert letto["s1"]["reason"]


def test_riscrivere_la_stessa_sorgente_sostituisce(tmp_path: Path) -> None:
    """Le regole cambiano: riclassificare non deve creare due verita'."""

    conn = _archivio(tmp_path)
    scrivi(conn, [Riga("s1", _attr("Vecchio"))])
    scrivi(conn, [Riga("s1", _attr("Nuovo"))])

    letto = leggi_tutte(conn)
    assert len(letto) == 1
    assert letto["s1"]["display_name"] == "Nuovo"


def test_una_riga_con_regole_vecchie_si_dichiara(tmp_path: Path) -> None:
    """Il valore c'e' ma non e' quello che il codice di oggi produrrebbe.

    Le regole sono cambiate due volte in un'ora di lavoro: da 58 nomi a 209 e poi
    a 54. Una riga scritta con regole vecchie che sembra aggiornata e' peggio di
    una riga assente.
    """

    conn = _archivio(tmp_path)
    scrivi(conn, [Riga("s1", _attr("App"))])
    conn.execute(f"UPDATE {TABELLA} SET rules_version = ?", (VERSIONE_REGOLE - 1,))

    assert leggi_tutte(conn)["s1"]["stale"] is True
    assert riepilogo(conn)["with_old_rules"] == 1


def test_il_riepilogo_conta_per_specie_e_progetti(tmp_path: Path) -> None:
    conn = _archivio(tmp_path)
    scrivi(conn, [
        Riga("s1", _attr("App")),
        Riga("s2", _attr("App")),
        Riga("s3", _attr("Altro")),
        Riga("s4", _attr(None, "agent_data", "agent_dir")),
        Riga("s5", _attr(None, "unassigned", "none")),
    ])

    r = riepilogo(conn)
    assert r["classified"] == 5
    assert r["by_kind"] == {"project": 3, "agent_data": 1, "unassigned": 1}
    # Due sorgenti dello stesso progetto contano UN progetto: si conta per
    # identificatore di cartella, non per riga.
    assert r["projects"] == 2


def test_creare_la_tabella_due_volte_non_e_un_errore(tmp_path: Path) -> None:
    conn = _archivio(tmp_path)
    assicura_tabella(conn)
    assicura_tabella(conn)
    assert tabella_esiste(conn)


def test_scrivere_zero_righe_non_crea_disordine(tmp_path: Path) -> None:
    conn = _archivio(tmp_path)
    assert scrivi(conn, []) == 0
    assert leggi_tutte(conn) == {}

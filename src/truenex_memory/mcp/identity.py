"""Identita' dell'archivio servito, e diario del server MCP su stderr.

Perche' esiste questo modulo.

Un server MCP stdio apre l'archivio indicato da `--project-root`, e finora non
lo diceva a nessuno. Su questa macchina erano vive tre istanze avviate in
momenti diversi con radici diverse: due sulla cartella utente (18.426 documenti)
e una sul repo del sorgente (171 documenti). Una chiamata che fallisce e la
stessa chiamata che riesce poco dopo possono essere state servite da processi
diversi, su archivi diversi — quindi «stessi parametri, ora funziona» non
dimostra niente. Lo ha fatto notare la revisione di Codex, e aveva ragione: la
diagnosi che avevo scritto poggiava su un'assunzione mai verificata.

Da qui due obblighi.

Il primo: ogni istanza deve dichiarare, in modo verificabile, QUALE archivio
serve. Non solo il percorso: anche un identificatore del file e un'impronta del
contenuto, perche' due percorsi uguali su macchine diverse non sono lo stesso
archivio e lo stesso percorso in due momenti diversi puo' avere contenuti
diversi.

Il secondo: il ciclo di vita di ogni richiesta va scritto da qualche parte.
Senza, distinguere «richiesta mai arrivata», «arrivata e non elaborata»,
«elaborata e risposta perduta» e «errore in scrittura» e' impossibile — e sono
quattro cause diverse con quattro rimedi diversi.

Il diario va su STDERR. Su stdout ci sono i messaggi JSON-RPC: una riga di
diagnostica la' dentro corrompe il protocollo.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from truenex_memory import __version__
from truenex_memory.core.config import DATA_DIR_NAME, DB_FILENAME


def _adesso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def identita_archivio(project_root: Path | str) -> dict[str, Any]:
    """Cosa sta servendo questa istanza, in forma verificabile.

    `archive_id` identifica il FILE: e' l'impronta del percorso canonico, quindi
    due istanze con lo stesso `archive_id` leggono lo stesso file su questa
    macchina. `content_fingerprint` identifica il CONTENUTO al momento della
    lettura: cambia quando l'archivio viene reindicizzato, cosi' due risposte
    che sembrano incoerenti si possono attribuire a due stati diversi invece che
    a un difetto.

    Un archivio assente o illeggibile viene DICHIARATO. La versione precedente
    non diceva niente e il sintomo era trovarci poca roba — e la poca roba non
    e' un errore riconoscibile.
    """

    radice = Path(project_root).resolve()
    db = radice / DATA_DIR_NAME / DB_FILENAME
    canonico = str(db).replace("\\", "/").casefold()

    voce: dict[str, Any] = {
        "project_root": str(radice),
        "db_path": str(db),
        "archive_id": hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:16],
        "db_exists": db.exists(),
        "db_size_bytes": 0,
        "documents": None,
        "chunks": None,
        "active_sources": None,
        "projects": None,
        "content_fingerprint": None,
        "unreadable": None,
    }

    if not db.exists():
        return voce

    voce["db_size_bytes"] = db.stat().st_size
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            documenti = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunk = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            sorgenti, progetti = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT project_name)"
                " FROM source_ledger WHERE status = 'active'"
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        voce["unreadable"] = f"{type(exc).__name__}: {exc}"
        return voce

    voce["documents"] = documenti
    voce["chunks"] = chunk
    voce["active_sources"] = sorgenti
    voce["projects"] = progetti
    voce["content_fingerprint"] = hashlib.sha256(
        f"{documenti}:{chunk}:{sorgenti}:{progetti}".encode("utf-8")
    ).hexdigest()[:16]
    return voce


def riga_archivio(identita: dict[str, Any]) -> str:
    """Una riga leggibile da un umano e da un modello.

    Va nelle `instructions` dell'handshake: e' il solo posto in cui chi fa le
    domande puo' vedere a quale archivio le sta facendo, senza chiamare un tool
    e senza fidarsi di un'assunzione.
    """

    if not identita["db_exists"]:
        return (
            f"ATTENZIONE: nessun archivio in {identita['db_path']}."
            f" Radice: {identita['project_root']}."
            " Le ricerche non troveranno niente perche' non c'e' niente da cercare,"
            " non perche' la risposta sia vuota."
        )
    if identita["unreadable"]:
        return (
            f"ATTENZIONE: archivio {identita['db_path']} illeggibile"
            f" ({identita['unreadable']})."
        )
    return (
        f"Archivio servito da questa istanza: {identita['db_path']}"
        f" — {identita['documents']} documenti, {identita['chunks']} chunk,"
        f" {identita['active_sources']} sorgenti attive su {identita['projects']} progetti"
        f" (archive_id {identita['archive_id']},"
        f" impronta contenuto {identita['content_fingerprint']})."
    )


def istruzioni(identita: dict[str, Any], profilo: str) -> str:
    """Il testo delle `instructions` dell'handshake.

    L'ordine e' deliberato: prima QUALE archivio, poi il profilo. Chi legge deve
    sapere a chi sta parlando prima di sapere come parlargli.
    """

    return riga_archivio(identita) + "\n\n" + profilo


class Diario:
    """Il diario del server, una riga JSON per evento, su stderr.

    Ogni riga porta il PID, perche' con piu' istanze vive attribuire un evento
    al processo giusto e' l'unica cosa che permette di correlare il log del
    client col nostro.

    Scrivere il diario non deve MAI far cadere il server: una riga di
    diagnostica persa non e' niente, una risposta persa lo e'.
    """

    def __init__(self, *, flusso: Any = None) -> None:
        self._flusso = flusso if flusso is not None else sys.stderr
        self._pid = os.getpid()

    def evento(self, nome: str, **campi: Any) -> None:
        try:
            riga = {"t": _adesso(), "pid": self._pid, "evento": nome, **campi}
            print(json.dumps(riga, separators=(",", ":"), default=str), file=self._flusso, flush=True)
        except Exception:  # pragma: no cover - il diario non comanda
            pass

    def avvio(self, identita: dict[str, Any]) -> None:
        self.evento(
            "server_start",
            ppid=os.getppid(),
            exe=sys.executable,
            version=__version__,
            archive=identita,
        )

    def uscita(self, motivo: str, **campi: Any) -> None:
        self.evento("server_exit", motivo=motivo, **campi)


class Cronometro:
    """Durata di una richiesta, in millisecondi interi."""

    def __init__(self) -> None:
        self._inizio = time.monotonic()

    def ms(self) -> int:
        return int((time.monotonic() - self._inizio) * 1000)

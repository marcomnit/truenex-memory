"""Mantenere aggiornato il grafo del codice, senza dipendere da nessun client.

## Perche' sta qui e non in un hook

Il grafo del codice e' un artefatto derivato: nasce dai sorgenti e invecchia
quando i sorgenti cambiano. La prima soluzione proposta era un hook di Claude
Code che lanciasse `graph build` dopo ogni modifica di file. Funzionava, ed era
sbagliata di disegno: truenex-memory serve Claude, Codex, Cursor, Kimi e
qualunque altro client MCP, quindi una automazione scritta nella configurazione
di uno dei cinque lascia gli altri quattro con un grafo vecchio e nessun
rimedio. Peggio: la stessa regola andrebbe riscritta cinque volte, in cinque
linguaggi di configurazione, e le cinque copie divergerebbero.

Un artefatto derivato deve sapere da solo di essere scaduto e provvedere. Qui
la logica sta in libreria, e le tre porte che leggono il grafo — il tool MCP
(quindi tutti i client), la CLI, l'API della GUI — chiamano la stessa funzione.
Nessun client ha bisogno di configurare niente.

## Perche' in disparte e non subito

Il confronto delle impronte costa 4 ms; la ricostruzione costa ~26 s su questo
progetto. Pagarla dentro la chiamata trasformerebbe una risposta immediata in
mezzo minuto di attesa, e chi ha fretta spegnerebbe il controllo — cioe'
l'automazione morirebbe per eccesso di zelo. Quindi: si risponde subito col
grafo che c'e', **dichiarando** che e' vecchio, e la ricostruzione parte in un
processo separato. La chiamata successiva trova il grafo nuovo.

## I due vincoli che nessun client perdona

**Il protocollo.** Un server MCP parla JSON-RPC su stdout. Un processo figlio
che eredita stdout inserisce le proprie righe nel protocollo e rompe la
sessione di *qualunque* client. Per questo il figlio nasce con stdin, stdout e
stderr su os.devnull: non e' prudenza generica, e' la condizione perche' questo
meccanismo possa stare in un server MCP.

**Lo schermo.** Un lavoro in disparte che apre finestre non e' in disparte. La
prima versione combinava `DETACHED_PROCESS` e `CREATE_NO_WINDOW`: Win32 ignora
il secondo quando c'e' il primo, quindi il processo restava senza console e
ogni worker del pool di estrazione se ne allocava una **visibile** — finestre
che lampeggiavano sullo schermo. Ora si usa `pythonw.exe` (sottosistema
grafico, nessuna console per lui ne' per i suoi figli) e solo
`CREATE_NO_WINDOW`. `DETACHED_PROCESS` non serviva: su Windows un figlio
sopravvive comunque al genitore.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from truenex_memory.graph.code_graph import FileGraph

# Quanto vale un lucchetto abbandonato prima di essere ignorato. Non e' una
# soglia misurata: e' il tempo oltre il quale una ricostruzione va considerata
# morta (il processo e' stato ucciso, la macchina riavviata) invece di bloccare
# per sempre ogni tentativo successivo. Generoso rispetto ai ~26 s di una
# ricostruzione reale, cosi' due client che chiedono insieme non si accavallano.
LOCK_STALE_AFTER_SECONDS = 30 * 60

# Nome della variabile che spegne il meccanismo. Acceso per default: un
# artefatto derivato che si aggiorna solo se qualcuno lo configura e' di nuovo
# un compito affidato alla memoria di una persona.
AUTO_REBUILD_ENV = "TRUENEX_GRAPH_AUTO_REBUILD"


def auto_rebuild_enabled() -> bool:
    """Vero se la ricostruzione in disparte e' attiva (default: si').

    Si spegne con ``TRUENEX_GRAPH_AUTO_REBUILD=0`` (o ``off``/``false``/``no``),
    che serve nei test, nelle esecuzioni non interattive e a chi preferisce
    ricostruire a mano.
    """

    raw = (os.environ.get(AUTO_REBUILD_ENV) or "").strip().lower()
    return raw not in {"0", "off", "false", "no"}


def _lock_path(cache_dir: Path, root: str) -> Path:
    from truenex_memory.graph.code_graph import cache_slug

    return cache_dir / f"{cache_slug(Path(root))}.rebuilding"


def _claim(lock: Path) -> bool:
    """Prende il lucchetto se libero. Vero se il chiamante deve ricostruire.

    Creazione esclusiva (``O_EXCL``): se due client scoprono lo stesso grafo
    vecchio nello stesso istante, uno solo ricostruisce e l'altro riceve
    "in corso" — senza questo, N client aperti sullo stesso progetto
    lancerebbero N estrazioni parallele sulla stessa cartella.
    """

    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_AFTER_SECONDS:
            lock.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - corsa con un altro processo
        pass
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:  # pragma: no cover - cache non scrivibile
        return False
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    return True


def _background_interpreter() -> str:
    """L'interprete senza console, quando esiste.

    Su Windows `python.exe` e' un programma di console: un processo che non ne
    ha una gliene fa allocare una nuova, **visibile**. La ricostruzione usa un
    pool di estrazione, quindi ogni worker ne apriva una — finestre che
    lampeggiano sullo schermo durante un lavoro che deve essere invisibile.
    `pythonw.exe` e' lo stesso interprete compilato per il sottosistema
    grafico: non alloca console, ne' lui ne' i figli che genera.
    """

    if os.name != "nt":
        return sys.executable
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else sys.executable


def _spawn_rebuild(root: str, cache_dir: Path, lock: Path) -> bool:
    """Lancia `graph build` in un processo separato e non lo aspetta."""

    command = [
        _background_interpreter(), "-m", "truenex_memory",
        "graph", "build", root, "--db", str(cache_dir.parent / "truenex_memory.db"),
    ]
    creation = 0
    if os.name == "nt":  # pragma: no cover - dipende dalla piattaforma
        # Solo CREATE_NO_WINDOW. Combinato con DETACHED_PROCESS veniva
        # **ignorato** (lo dice la documentazione Win32: CREATE_NO_WINDOW non
        # ha effetto insieme a DETACHED_PROCESS o CREATE_NEW_CONSOLE), e il
        # processo staccato senza console faceva allocare una finestra a
        # ciascun worker del pool. Un processo figlio su Windows sopravvive
        # comunque al genitore, quindi DETACHED_PROCESS non serviva a niente.
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(  # noqa: S603 - comando costruito qui, non da input
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation,
            close_fds=True,
        )
    except OSError:
        lock.unlink(missing_ok=True)
        return False
    return True


def ensure_current(
    graph: FileGraph, cache_dir: Path, *, allow_rebuild: bool | None = None
) -> dict[str, Any]:
    """Confronta il grafo coi sorgenti e, se e' vecchio, lo fa ricostruire.

    Non blocca: restituisce sempre subito. Il valore descrive cosa ha trovato e
    cosa ha fatto, e va messo nella risposta della porta che l'ha chiamata —
    perche' rispondere con un grafo vecchio senza dirlo e' il difetto che questo
    modulo esiste per togliere, e la ricostruzione appena avviata non aiuta chi
    sta leggendo *questa* risposta.

    ``rebuild`` vale ``"non necessario"``, ``"avviata"``, ``"in corso"``
    (un altro client ci sta gia' pensando), ``"disattivata"`` o
    ``"non avviata"`` (il lancio e' fallito).
    """

    freshness = graph.staleness()
    result: dict[str, Any] = dict(freshness)
    if freshness.get("stale") is None:
        # Grafo di una versione precedente: non porta l'impronta, quindi non si
        # sa se e' vecchio. Ricostruirlo e' l'unica risposta utile.
        result["rebuild"] = "non necessario"
        freshness = {"stale": True}
    if not freshness.get("stale"):
        result["rebuild"] = "non necessario"
        return result

    enabled = auto_rebuild_enabled() if allow_rebuild is None else allow_rebuild
    if not enabled:
        result["rebuild"] = "disattivata"
        result["hint"] = f'truenex-mem graph build "{graph.root}"'
        return result

    lock = _lock_path(cache_dir, graph.root)
    if not _claim(lock):
        result["rebuild"] = "in corso"
        return result
    result["rebuild"] = "avviata" if _spawn_rebuild(graph.root, cache_dir, lock) else "non avviata"
    if result["rebuild"] == "non avviata":
        result["hint"] = f'truenex-mem graph build "{graph.root}"'
    return result


def release_lock(cache_dir: Path, root: str) -> None:
    """Toglie il lucchetto. Chiamato al termine di `graph build`."""

    _lock_path(cache_dir, root).unlink(missing_ok=True)

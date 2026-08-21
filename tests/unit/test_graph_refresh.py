"""La freschezza del grafo e' un problema della libreria, non dei client.

Perche' questo file esiste: la prima automazione proposta per «ricostruisci il
grafo dopo aver cambiato il codice» era un hook di Claude Code. Funzionava, ed
era sbagliata di disegno — truenex-memory serve Claude, Codex, Cursor, Kimi e
qualunque client MCP, quindi la regola scritta nella configurazione di uno
lascia gli altri con un grafo vecchio, e riscritta in cinque posti divergerebbe.

Questi test difendono la proprieta' che rende l'automazione trasversale: la
decisione sta in `graph/refresh.py`, e le tre porte che leggono il grafo — tool
MCP, CLI, API della GUI — chiamano la stessa funzione senza politiche proprie.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from truenex_memory.graph import FileEdge, FileGraph, auto_rebuild_enabled, ensure_current, release_lock
from truenex_memory.graph.refresh import LOCK_STALE_AFTER_SECONDS, _lock_path


def _tree(root: Path, *names: str) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def f():\n    return 1\n", encoding="utf-8")


def _graph_for(root: Path) -> FileGraph:
    fingerprint, directories = {}, {root}
    for path in sorted(root.rglob("*.py")):
        info = path.stat()
        fingerprint[path.relative_to(root).as_posix()] = f"{info.st_mtime_ns}:{info.st_size}"
        directories.add(path.parent)
    return FileGraph(
        root=root.as_posix(),
        edges=[FileEdge("a.py", "b.py", "calls", 1)],
        fingerprint=fingerprint,
        dir_fingerprint={
            ("." if d == root else d.relative_to(root).as_posix()): str(d.stat().st_mtime_ns)
            for d in directories
        },
    )


def test_a_current_graph_triggers_nothing(tmp_path: Path) -> None:
    """Il costo a regime deve essere il solo confronto delle impronte."""

    _tree(tmp_path, "a.py")
    graph = _graph_for(tmp_path)
    cache = tmp_path / "cache"

    result = ensure_current(graph, cache, allow_rebuild=True)

    assert result["stale"] is False
    assert result["rebuild"] == "non necessario"
    assert not _lock_path(cache, graph.root).exists()


def test_a_stale_graph_starts_a_rebuild(tmp_path: Path) -> None:
    _tree(tmp_path, "a.py")
    graph = _graph_for(tmp_path)
    cache = tmp_path / "cache"
    (tmp_path / "a.py").write_text("def f():\n    return 1 + 1\n", encoding="utf-8")

    result = ensure_current(graph, cache, allow_rebuild=True)

    assert result["stale"] is True
    assert result["rebuild"] == "avviata"
    release_lock(cache, graph.root)


def test_two_clients_do_not_both_rebuild(tmp_path: Path) -> None:
    """Il caso che rende necessario il lucchetto.

    Con Claude, Codex e la GUI aperti sullo stesso progetto, tre porte scoprono
    lo stesso grafo vecchio nello stesso istante. Senza creazione esclusiva
    partirebbero tre estrazioni sulla stessa cartella.
    """

    _tree(tmp_path, "a.py")
    graph = _graph_for(tmp_path)
    cache = tmp_path / "cache"
    (tmp_path / "a.py").write_text("def f():\n    return 1 + 1\n", encoding="utf-8")

    primo = ensure_current(graph, cache, allow_rebuild=True)
    secondo = ensure_current(graph, cache, allow_rebuild=True)
    terzo = ensure_current(graph, cache, allow_rebuild=True)

    assert primo["rebuild"] == "avviata"
    assert secondo["rebuild"] == "in corso"
    assert terzo["rebuild"] == "in corso"
    release_lock(cache, graph.root)


def test_an_abandoned_lock_is_reclaimed(tmp_path: Path) -> None:
    """Un processo ucciso non deve bloccare le ricostruzioni per sempre.

    Senza scadenza, una macchina riavviata a metà estrazione lascerebbe il
    grafo di quel progetto senza rimedio automatico, e nessuno se ne
    accorgerebbe: e' proprio il difetto che questo meccanismo rimuove.
    """

    _tree(tmp_path, "a.py")
    graph = _graph_for(tmp_path)
    cache = tmp_path / "cache"
    (tmp_path / "a.py").write_text("def f():\n    return 1 + 1\n", encoding="utf-8")
    lock = _lock_path(cache, graph.root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("99999", encoding="utf-8")
    vecchio = time.time() - LOCK_STALE_AFTER_SECONDS - 60
    os.utime(lock, (vecchio, vecchio))

    result = ensure_current(graph, cache, allow_rebuild=True)

    assert result["rebuild"] == "avviata"
    release_lock(cache, graph.root)


def test_the_rebuild_can_be_switched_off(tmp_path: Path) -> None:
    """Chi preferisce ricostruire a mano riceve il comando, non un processo."""

    _tree(tmp_path, "a.py")
    graph = _graph_for(tmp_path)
    (tmp_path / "a.py").write_text("def f():\n    return 1 + 1\n", encoding="utf-8")

    result = ensure_current(graph, tmp_path / "cache", allow_rebuild=False)

    assert result["rebuild"] == "disattivata"
    assert "graph build" in result["hint"]


def test_it_is_on_by_default(monkeypatch) -> None:
    """Un artefatto derivato che si aggiorna solo se configurato non e' automatico."""

    monkeypatch.delenv("TRUENEX_GRAPH_AUTO_REBUILD", raising=False)
    assert auto_rebuild_enabled() is True


@pytest.mark.parametrize("valore", ["0", "off", "false", "no", "OFF"])
def test_the_switch_accepts_the_usual_spellings(monkeypatch, valore: str) -> None:
    monkeypatch.setenv("TRUENEX_GRAPH_AUTO_REBUILD", valore)
    assert auto_rebuild_enabled() is False


def test_a_graph_without_a_fingerprint_is_rebuilt(tmp_path: Path) -> None:
    """Un grafo di una versione precedente non sa di essere vecchio.

    Non sapere non e' essere aggiornato: lasciarlo com'e' significherebbe
    rispondere per sempre sul codice di allora.
    """

    graph = FileGraph(root=tmp_path.as_posix(), edges=[FileEdge("a.py", "b.py", "calls", 1)])
    cache = tmp_path / "cache"

    result = ensure_current(graph, cache, allow_rebuild=True)

    assert result["rebuild"] == "avviata"
    release_lock(cache, graph.root)


def test_the_child_never_inherits_the_protocol_stream(tmp_path: Path) -> None:
    """Il vincolo che nessun client perdona.

    Un server MCP parla JSON-RPC su stdout. Un figlio che eredita stdout
    inserisce le sue righe nel protocollo e rompe la sessione di QUALUNQUE
    client: non e' prudenza generica, e' la condizione perche' questo
    meccanismo possa vivere dentro un server MCP.
    """

    import subprocess

    from truenex_memory.graph import refresh

    visti: dict = {}

    class FintoPopen:
        def __init__(self, comando, **kwargs):
            visti.update(comando=comando, **kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(refresh.subprocess, "Popen", FintoPopen)
    try:
        refresh._spawn_rebuild(tmp_path.as_posix(), tmp_path / "cache", tmp_path / "lock")
    finally:
        monkey.undo()

    assert visti["stdout"] is subprocess.DEVNULL
    assert visti["stderr"] is subprocess.DEVNULL
    assert visti["stdin"] is subprocess.DEVNULL
    assert visti["comando"][1:3] == ["-m", "truenex_memory"], (
        "rilanciarsi con `python -m` e non con lo script console: dentro un "
        "server MCP la cartella degli script non e' nota, sys.executable si'"
    )


def test_the_background_process_shows_no_window(tmp_path: Path) -> None:
    """Un lavoro in disparte che apre finestre non e' in disparte.

    Marco le ha viste lampeggiare: `DETACHED_PROCESS` insieme a
    `CREATE_NO_WINDOW` fa ignorare il secondo (documentazione Win32), quindi il
    processo restava senza console e ogni worker del pool se ne allocava una
    visibile. Il test fissa le due condizioni che lo evitano.
    """

    import subprocess

    from truenex_memory.graph import refresh

    visti: dict = {}

    class FintoPopen:
        def __init__(self, comando, **kwargs):
            visti.update(comando=comando, **kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(refresh.subprocess, "Popen", FintoPopen)
    try:
        refresh._spawn_rebuild(tmp_path.as_posix(), tmp_path / "cache", tmp_path / "lock")
    finally:
        monkey.undo()

    if os.name == "nt":
        assert Path(visti["comando"][0]).name == "pythonw.exe", (
            "python.exe e' un programma di console: senza una console propria "
            "Windows gliene alloca una visibile"
        )
        detached = getattr(subprocess, "DETACHED_PROCESS", 0)
        assert not (visti["creationflags"] & detached), (
            "DETACHED_PROCESS fa ignorare CREATE_NO_WINDOW, e non serve: "
            "un figlio sopravvive comunque al genitore"
        )
        assert visti["creationflags"] & subprocess.CREATE_NO_WINDOW

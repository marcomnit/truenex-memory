"""Il grafo si accorge da solo di essere piu' vecchio del codice.

Perche' esiste: un grafo del codice e' una fotografia, e una fotografia
risponde sul passato senza dichiararlo — una risposta sbagliata
indistinguibile da una giusta. La versione precedente lo risolveva con una
frase all'utente («ricordati di ricostruirlo dopo aver cambiato il codice»),
che e' un compito dato a una persona al posto di una verifica fatta dalla
macchina. Questi test coprono la verifica.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from truenex_memory.graph import FileEdge, FileGraph


def _tree(root: Path, *names: str) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def f():\n    return 1\n", encoding="utf-8")


def _graph_for(root: Path) -> FileGraph:
    """Costruisce l'impronta come fa `build_file_graph`, senza l'estrattore."""

    fingerprint, directories = {}, {root}
    for path in sorted(root.rglob("*.py")):
        info = path.stat()
        fingerprint[path.relative_to(root).as_posix()] = f"{info.st_mtime_ns}:{info.st_size}"
        parent = path.parent
        while parent != root and root in parent.parents:
            directories.add(parent)
            parent = parent.parent
        directories.add(parent)
    dir_fingerprint = {
        ("." if d == root else d.relative_to(root).as_posix()): str(d.stat().st_mtime_ns)
        for d in directories
    }
    return FileGraph(
        root=root.as_posix(),
        edges=[FileEdge("a.py", "pkg/b.py", "calls", 1)],
        fingerprint=fingerprint,
        dir_fingerprint=dir_fingerprint,
    )


def test_an_untouched_tree_is_not_stale(tmp_path: Path) -> None:
    _tree(tmp_path, "a.py", "pkg/b.py")

    assert _graph_for(tmp_path).staleness()["stale"] is False


def test_a_modified_file_is_detected(tmp_path: Path) -> None:
    _tree(tmp_path, "a.py", "pkg/b.py")
    graph = _graph_for(tmp_path)

    (tmp_path / "a.py").write_text("def f():\n    return 1 + 1 + 1\n", encoding="utf-8")

    result = graph.staleness()
    assert result["stale"] is True
    assert "a.py" in result["changed"]


def test_the_blind_spot_is_one_clock_tick_wide(tmp_path: Path) -> None:
    """Il limite del metodo, scritto perche' non se ne perda memoria.

    mtime + dimensione non e' un hash: una modifica che lascia la dimensione
    identica E cade nello stesso scatto dell'orologio di sistema (~16 ms su
    Windows) non si vede. Prenderla richiederebbe di leggere il contenuto di
    tutti i sorgenti a ogni controllo, cioe' rinunciare a farlo a ogni lettura
    — e un controllo che non si fa non protegge da niente.

    In pratica la finestra e' quella fra la fine della costruzione del grafo e
    16 ms dopo: nessuna modifica umana ci cade. Questo test la fissa come
    scelta consapevole, non come sorpresa: se un domani l'impronta passasse a
    un hash del contenuto, qui si vedrebbe cambiare il comportamento.
    """

    _tree(tmp_path, "a.py")
    path = tmp_path / "a.py"
    graph = _graph_for(tmp_path)

    before = path.stat().st_mtime_ns
    path.write_text("def f():\n    return 2\n", encoding="utf-8")  # stessa dimensione
    after = path.stat().st_mtime_ns

    if after == before:
        assert graph.staleness()["stale"] is False, (
            "dentro lo stesso scatto d'orologio, a dimensione uguale, non si vede: "
            "e' il limite dichiarato del metodo"
        )
    else:
        assert graph.staleness()["stale"] is True


def test_a_file_touched_without_changing_size_is_detected(tmp_path: Path) -> None:
    """La dimensione da sola non basta: l'impronta porta anche l'mtime."""

    _tree(tmp_path, "a.py")
    graph = _graph_for(tmp_path)

    later = time.time() + 120
    os.utime(tmp_path / "a.py", (later, later))

    assert graph.staleness()["stale"] is True


def test_a_deleted_file_is_detected(tmp_path: Path) -> None:
    _tree(tmp_path, "a.py", "pkg/b.py")
    graph = _graph_for(tmp_path)

    (tmp_path / "pkg" / "b.py").unlink()

    result = graph.staleness()
    assert result["counts"]["missing"] == 1
    assert "pkg/b.py" in result["missing"]


def test_a_new_file_is_detected_through_the_directory_mtime(tmp_path: Path) -> None:
    """Il caso che l'impronta dei soli file NON vedrebbe.

    Un file aggiunto non e' fra quelli noti, quindi nessun confronto lo
    riguarda. Elencare l'albero per scoprirlo costa 11,5 s su questo progetto:
    a quel prezzo il controllo non si potrebbe fare a ogni lettura, cioe' non
    servirebbe a niente. L'mtime della cartella che lo contiene da' la stessa
    risposta con un `stat`.
    """

    _tree(tmp_path, "pkg/b.py")
    graph = _graph_for(tmp_path)

    later = time.time() + 120
    (tmp_path / "pkg" / "nuovo.py").write_text("x = 1\n", encoding="utf-8")
    os.utime(tmp_path / "pkg", (later, later))

    result = graph.staleness()
    assert result["stale"] is True
    assert "pkg" in result["tree"]


def test_a_graph_without_a_fingerprint_says_it_does_not_know(tmp_path: Path) -> None:
    """Un grafo di una versione precedente non finge di essere aggiornato.

    `stale: None` e' diverso da `stale: False`. Confonderli reintrodurrebbe
    esattamente il difetto: un grafo vecchio che si dichiara fresco.
    """

    graph = FileGraph(root=tmp_path.as_posix(), edges=[FileEdge("a.py", "b.py", "calls", 1)])

    result = graph.staleness()
    assert result["stale"] is None
    assert "impronta" in result["reason"]


def test_the_check_reads_metadata_only(tmp_path: Path) -> None:
    """Il costo deve restare quello di uno `stat` per file, non di una lettura.

    Se qualcuno reintroducesse la scansione dell'albero o l'hash del
    contenuto, il controllo tornerebbe a costare secondi e verrebbe spento —
    e con lui la garanzia. Il test lo fissa: 400 file sotto un decimo di
    secondo.
    """

    _tree(tmp_path, *(f"pkg{i // 40}/mod{i}.py" for i in range(400)))
    graph = _graph_for(tmp_path)

    start = time.perf_counter()
    graph.staleness()
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1, f"il controllo ha richiesto {elapsed:.3f} s"

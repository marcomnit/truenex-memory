"""`/api/project-graph` per un progetto che ha SOLO il grafo del codice.

Il difetto. L'endpoint costruiva i nodi dal `source_ledger` — i documenti
indicizzati — e poi ci attaccava gli archi del grafo. Per un progetto non
indicizzato non c'era niente da decorare, quindi restituiva 0 nodi e 0 archi:
cliccandolo nell'app non si vedeva nulla. Misurato su una macchina vera:
WebForm aveva 2.994 file e 3.890 archi in cache, e l'endpoint rispondeva zero.

Ora i nodi sono i FILE del grafo e gli archi le relazioni fra loro, che e'
l'unica cosa che quel progetto ha e la piu' utile. La risposta dichiara
`source: "code_graph"`, perche' un grafo del codice non e' un indice di
documenti e confonderli porta a cercare testo che non esiste.
"""

from __future__ import annotations

import json
from pathlib import Path

from truenex_memory.graph import CACHE_VERSION, FileEdge, FileGraph, save_file_graph
from truenex_memory.serve import _grafo_dal_solo_codice


def _cache_con_grafo(tmp_path: Path, nome: str, archi: list[FileEdge]) -> Path:
    """Scrive un grafo in una cache accanto a un database finto.

    `default_cache_dirs(db)` guarda per prima `db.parent / "code_graphs"`:
    mettendolo la', il test non dipende dalla cartella utente della macchina.
    """

    radice = tmp_path / nome
    (radice / "src").mkdir(parents=True, exist_ok=True)
    grafo = FileGraph(
        root=radice.as_posix(),
        edges=archi,
        stats={"files": 3, "file_edges": len(archi), "entity_edges": len(archi) * 4},
        fingerprint={"src/a.py": "1:1", "src/b.py": "1:1", "src/orfano.py": "1:1"},
    )
    db = tmp_path / ".truenex-memory" / "truenex_memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    save_file_graph(grafo, db.parent / "code_graphs")
    return db


def test_i_nodi_sono_i_file_del_grafo(tmp_path: Path) -> None:
    db = _cache_con_grafo(tmp_path, "SoloCodice", [
        FileEdge("src/a.py", "src/b.py", "calls", 7),
    ])

    r = _grafo_dal_solo_codice("SoloCodice", db)

    assert r is not None, "il grafo esiste in cache ma non e' stato trovato"
    assert r["source"] == "code_graph"
    assert len(r["edges"]) == 1
    assert r["edges"][0] == {
        "source": "src/a.py", "target": "src/b.py",
        "relation_type": "calls", "weight": 7,
    }


def test_un_file_senza_relazioni_non_scompare(tmp_path: Path) -> None:
    """`src/orfano.py` non compare in nessun arco.

    Prendere i nodi solo dagli archi lo avrebbe fatto sparire in silenzio: un
    file che esiste e non chiama nessuno non e' un file che non esiste.
    """

    db = _cache_con_grafo(tmp_path, "ConOrfano", [
        FileEdge("src/a.py", "src/b.py", "imports", 1),
    ])

    r = _grafo_dal_solo_codice("ConOrfano", db)

    percorsi = {n["path"] for n in r["nodes"]}
    assert "src/orfano.py" in percorsi, "il file senza relazioni e' stato perso"
    assert percorsi == {"src/a.py", "src/b.py", "src/orfano.py"}


def test_ogni_arco_punta_a_un_nodo_che_esiste(tmp_path: Path) -> None:
    """Il frontend disegna gli archi per identificatore: uno che punta a un nodo
    assente viene scartato da cytoscape SENZA un errore."""

    db = _cache_con_grafo(tmp_path, "Coerente", [
        FileEdge("src/a.py", "src/b.py", "calls", 2),
        FileEdge("src/b.py", "src/c.py", "imports", 3),
    ])

    r = _grafo_dal_solo_codice("Coerente", db)

    ids = {n["id"] for n in r["nodes"]}
    for arco in r["edges"]:
        assert arco["source"] in ids, f"arco da {arco['source']} inesistente"
        assert arco["target"] in ids, f"arco verso {arco['target']} inesistente"


def test_il_riepilogo_conta_i_tipi_di_file(tmp_path: Path) -> None:
    db = _cache_con_grafo(tmp_path, "Misto", [
        FileEdge("src/a.py", "web/pagina.ts", "calls", 1),
    ])

    r = _grafo_dal_solo_codice("Misto", db)

    assert r["summary"]["total_files"] == len(r["nodes"])
    assert r["summary"]["breakdown"].get("py", 0) >= 1
    # Nessun chunk e nessun token: il progetto non e' indicizzato, e zero e' la
    # risposta vera invece di un numero inventato.
    assert r["summary"]["total_chunks"] == 0
    assert r["summary"]["total_tokens"] == 0


def test_nessun_grafo_con_quel_nome_da_None(tmp_path: Path) -> None:
    """`None` e non una risposta vuota: l'endpoint deve poter proseguire col suo
    percorso normale invece di restituire un grafo inventato."""

    db = _cache_con_grafo(tmp_path, "Esistente", [])

    assert _grafo_dal_solo_codice("MaiVisto", db) is None


def test_un_grafo_illeggibile_lo_dichiara(tmp_path: Path) -> None:
    """Il grafo c'e' ma non si legge: si dice, non si finge che manchi."""

    db = tmp_path / ".truenex-memory" / "truenex_memory.db"
    cache = db.parent / "code_graphs"
    cache.mkdir(parents=True)
    radice = tmp_path / "Rotto"
    radice.mkdir()
    (cache / "rotto.json").write_text(
        json.dumps({"cache_version": CACHE_VERSION - 1, "root": radice.as_posix(),
                    "stats": {"files": 9}}),
        encoding="utf-8",
    )

    r = _grafo_dal_solo_codice("Rotto", db)

    assert r is not None
    assert r["code_graph"]["status"] == "old_version"
    assert r["code_graph"]["detail"]
    assert r["nodes"] == [] and r["edges"] == []

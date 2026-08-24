"""L'elenco dei grafi in cache, e cio' che dichiara quando non riesce a leggerli.

Perche' esiste. L'app desktop mostrava UN progetto mentre in cache ce n'erano
sei: `/api/projects` restituisce una voce sola, costruita dal `project_root` con
cui il server e' stato avviato, e non esisteva alcun modo di chiedere gli altri
grafi. Non era un difetto di grafica: era un dato che non arrivava.

L'enumerazione viveva solo dentro `graph status`, scritta a mano con un glob.
Ora sta nel pacchetto e la usano sia la CLI sia `/api/code-graphs` — una sola
scansione, perche' due copie della stessa divergono e una delle due finisce per
nascondere cio' che l'altra mostra.

I numeri di questi test sono INVENTATI di proposito: quelli della macchina di
Marco (6 grafi, 693 file, 2.013 archi) non sono riproducibili in CI. Li si
verifica a mano contro `/api/code-graphs`, e sono scritti nel commit.
"""

from __future__ import annotations

import json
from pathlib import Path

from truenex_memory.graph import CACHE_VERSION, list_cached_graphs


def _scrivi(cache: Path, nome: str, radice: Path, **stats) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    percorso = cache / f"{nome}.json"
    percorso.write_text(
        json.dumps(
            {
                "cache_version": CACHE_VERSION,
                "root": radice.as_posix(),
                "edges": [],
                "stats": {"files": 0, "file_edges": 0, "entity_edges": 0, **stats},
            }
        ),
        encoding="utf-8",
    )
    return percorso


def test_una_cache_assente_non_e_un_errore(tmp_path: Path) -> None:
    assert list_cached_graphs(tmp_path / "non-esiste") == []


def test_elenca_i_grafi_con_i_loro_conteggi(tmp_path: Path) -> None:
    cache = tmp_path / "code_graphs"
    for nome, file, archi in (("alfa", 10, 20), ("beta", 5, 3)):
        radice = tmp_path / nome
        radice.mkdir()
        _scrivi(cache, nome, radice, files=file, file_edges=archi, entity_edges=archi * 7)

    voci = list_cached_graphs(cache)

    assert [v.name for v in voci] == ["alfa", "beta"]
    assert [v.files for v in voci] == [10, 5]
    assert [v.file_edges for v in voci] == [20, 3]
    assert all(v.status == "ok" for v in voci)
    assert sum(v.files for v in voci) == 15


def test_un_file_illeggibile_resta_nell_elenco_con_la_ragione(tmp_path: Path) -> None:
    """Il punto per cui questa funzione esiste.

    Il vecchio `graph status` faceva `continue` su un JSON rotto: il grafo
    spariva dall'elenco senza una parola, e un grafo che manca in silenzio e'
    indistinguibile da un progetto che non c'e'.
    """

    cache = tmp_path / "code_graphs"
    radice = tmp_path / "buono"
    radice.mkdir()
    _scrivi(cache, "buono", radice, files=4)
    (cache / "rotto.json").write_text("{ questo non e' json", encoding="utf-8")

    voci = list_cached_graphs(cache)

    assert len(voci) == 2, "il file rotto e' stato nascosto"
    rotto = next(v for v in voci if v.name == "rotto")
    assert rotto.status == "unreadable"
    assert rotto.detail, "senza la ragione, lo stato non serve a niente"
    assert rotto.files == 0


def test_una_cache_di_versione_precedente_e_dichiarata(tmp_path: Path) -> None:
    cache = tmp_path / "code_graphs"
    cache.mkdir(parents=True)
    radice = tmp_path / "vecchio"
    radice.mkdir()
    (cache / "vecchio.json").write_text(
        json.dumps(
            {
                "cache_version": CACHE_VERSION - 1,
                "root": radice.as_posix(),
                "stats": {"files": 99, "file_edges": 99},
            }
        ),
        encoding="utf-8",
    )

    voce = list_cached_graphs(cache)[0]

    assert voce.status == "old_version"
    assert str(CACHE_VERSION) in (voce.detail or "")
    assert voce.files == 0, "i conteggi di una cache vecchia non sono affidabili"


def test_una_radice_scomparsa_e_dichiarata(tmp_path: Path) -> None:
    """Trovato su una macchina vera: la cartella di un progetto puo' essere
    stata rinominata o cancellata dopo la costruzione del grafo."""

    cache = tmp_path / "code_graphs"
    _scrivi(cache, "fantasma", tmp_path / "cartella-mai-esistita", files=7)

    voce = list_cached_graphs(cache)[0]

    assert voce.status == "root_missing"
    assert voce.root_exists is False
    assert voce.files == 7, "il grafo c'e' ancora: e' la cartella che manca"


def test_to_dict_porta_tutto_cio_che_serve_all_interfaccia(tmp_path: Path) -> None:
    cache = tmp_path / "code_graphs"
    radice = tmp_path / "progetto"
    radice.mkdir()
    _scrivi(cache, "progetto", radice, files=3, file_edges=8, entity_edges=40)

    d = list_cached_graphs(cache)[0].to_dict()

    assert set(d) == {
        "root", "name", "cache_file", "files", "file_edges",
        "entity_edges", "status", "detail", "root_exists",
    }
    assert d["files"] == 3 and d["file_edges"] == 8 and d["entity_edges"] == 40

# ─── i casi che la revisione ha indicato come mancanti ───

def test_conteggi_malformati_non_abbattono_l_elenco(tmp_path: Path) -> None:
    """Trovato dalla revisione di DeepSeek.

    `int(stats.get("files"))` andava in ValueError su un valore non numerico e
    faceva cadere l'INTERA enumerazione: una sola cache scritta male nascondeva
    tutti gli altri grafi. Cioe' esattamente il contrario di cio' che questa
    funzione promette.
    """

    cache = tmp_path / "code_graphs"
    cache.mkdir(parents=True)
    buona = tmp_path / "buona"
    buona.mkdir()
    _scrivi(cache, "buona", buona, files=11)
    malformata = tmp_path / "malformata"
    malformata.mkdir()
    (cache / "malformata.json").write_text(
        json.dumps({
            "cache_version": CACHE_VERSION,
            "root": malformata.as_posix(),
            "stats": {"files": "molti", "file_edges": [1, 2], "entity_edges": None},
        }),
        encoding="utf-8",
    )

    voci = list_cached_graphs(cache)

    assert len(voci) == 2, "una cache malformata ha nascosto l'altra"
    m = next(v for v in voci if v.name == "malformata")
    assert (m.files, m.file_edges, m.entity_edges) == (0, 0, 0)
    assert next(v for v in voci if v.name == "buona").files == 11


def test_un_json_che_non_e_un_oggetto(tmp_path: Path) -> None:
    cache = tmp_path / "code_graphs"
    cache.mkdir(parents=True)
    (cache / "lista.json").write_text("[1, 2, 3]", encoding="utf-8")

    voce = list_cached_graphs(cache)[0]

    assert voce.status == "unreadable"
    assert "list" in (voce.detail or "")


def test_illeggibile_non_afferma_che_la_radice_manchi(tmp_path: Path) -> None:
    """«Non so» e «non c'e'» sono due risposte diverse.

    Per un file che non si legge la radice non e' conoscibile: `root_exists`
    deve essere None, non False.
    """

    cache = tmp_path / "code_graphs"
    cache.mkdir(parents=True)
    (cache / "rotto.json").write_text("{ rotto", encoding="utf-8")

    assert list_cached_graphs(cache)[0].root_exists is None


def test_il_nome_regge_i_percorsi_di_windows(tmp_path: Path) -> None:
    """Le cache si scambiano fra macchine: il separatore non si da' per scontato."""

    cache = tmp_path / "code_graphs"
    cache.mkdir(parents=True)
    (cache / "win.json").write_text(
        json.dumps({
            "cache_version": CACHE_VERSION,
            "root": r"D:\Project_sw\ProjectVs\Blazor",
            "stats": {"files": 14, "file_edges": 15},
        }),
        encoding="utf-8",
    )

    assert list_cached_graphs(cache)[0].name == "Blazor"


def test_stats_arriva_intero_e_non_solo_tre_chiavi(tmp_path: Path) -> None:
    """La regressione peggiore di questa modifica, trovata dalla revisione.

    `graph status --json` mostrava `**graph.stats`, che porta anche
    `skipped_by_suffix`, `skipped_total` e `relations`: sono i limiti che il
    grafo DICHIARA. Tenerne solo tre chiavi li buttava in silenzio — cioe'
    rompeva la funzione dell'onestà mentre se ne aggiungeva un'altra.
    """

    cache = tmp_path / "code_graphs"
    cache.mkdir(parents=True)
    radice = tmp_path / "progetto"
    radice.mkdir()
    (cache / "progetto.json").write_text(
        json.dumps({
            "cache_version": CACHE_VERSION,
            "root": radice.as_posix(),
            "stats": {
                "files": 3,
                "file_edges": 8,
                "entity_edges": 40,
                "skipped_total": 460,
                "skipped_by_suffix": {".vb": 79},
                "relations": {"calls": 8},
            },
        }),
        encoding="utf-8",
    )

    voce = list_cached_graphs(cache)[0]

    assert voce.stats["skipped_total"] == 460
    assert voce.stats["skipped_by_suffix"] == {".vb": 79}
    assert voce.stats["relations"] == {"calls": 8}


def test_gli_stati_sono_un_enumerato_stabile_e_ascii(tmp_path: Path) -> None:
    """Un client scrive confronti su `status`: dev'essere un contratto, non
    un'etichetta d'interfaccia. L'italiano sta in `detail`."""

    cache = tmp_path / "code_graphs"
    cache.mkdir(parents=True)
    (cache / "a.json").write_text("{ rotto", encoding="utf-8")
    _scrivi(cache, "b", tmp_path / "mai-esistita", files=1)
    buona = tmp_path / "buona"
    buona.mkdir()
    _scrivi(cache, "c", buona, files=2)

    stati = {v.status for v in list_cached_graphs(cache)}

    assert stati <= {"ok", "unreadable", "old_version", "root_missing"}
    assert all(s.isascii() for s in stati)

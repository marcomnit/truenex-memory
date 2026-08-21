"""Un limite puo' stare nel software; un limite invisibile no.

Perche' questo file esiste: mettere un tetto a una risposta e' spesso giusto —
questo strumento alimenta un agente che paga a token. Ma un tetto che non si
dichiara cambia il significato della risposta: «chi chiama questa funzione» con
37 chiamanti e 12 mostrati non e' una risposta parziale, e' una risposta
SBAGLIATA, perche' chi la legge non ha modo di sapere che manca qualcosa.
La regola che questi test difendono: il numero vero viaggia sempre accanto a
quello mostrato, e ogni scarto silenzioso e' un difetto.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.graph import (
    DEFAULT_CODE_SUFFIXES,
    EXPLAIN_GROUP_LIMIT,
    EntityEdge,
    FileGraph,
    code_suffixes,
    collect_source_files,
    explain_entity,
)


def _edge(source: str, target: str, relation: str = "calls") -> EntityEdge:
    return EntityEdge(
        source=source, target=target, relation_type=relation,
        source_file=source.split("::")[0], target_file=target.split("::")[0],
    )


# ── il taglio si dichiara ─────────────────────────────────────────────────

def test_the_real_count_travels_next_to_the_shown_one() -> None:
    graph = FileGraph(
        root="/repo",
        entities=[_edge(f"src/m{i}.py::chiamante{i}", "src/core.py::bersaglio") for i in range(30)],
    )

    result = explain_entity(graph, "bersaglio", limit=5)

    assert len(result["callers"]) == 5
    assert result["totals"]["callers"] == 30, "il conteggio si fa PRIMA di tagliare"
    assert result["truncated"]["callers"] == 30


def test_nothing_is_marked_truncated_when_nothing_was_cut() -> None:
    """`truncated` vuoto e' l'unica prova che la risposta e' completa."""

    graph = FileGraph(root="/repo", entities=[_edge("src/a.py::uno", "src/core.py::bersaglio")])

    result = explain_entity(graph, "bersaglio", limit=5)

    assert result["truncated"] == {}
    assert result["totals"]["callers"] == 1


def test_two_docstrings_that_start_alike_stay_distinct() -> None:
    """La chiave di deduplica era tagliata agli 80 caratteri iniziali.

    Due spiegazioni diverse che cominciano con la stessa frase — frequente in
    un progetto con uno stile di docstring uniforme — venivano fuse in una, e
    la seconda spariva senza che niente lo segnalasse. Un tetto sui caratteri
    di una CHIAVE non e' un risparmio di token: e' perdita di dati.
    """

    comune = "Restituisce il risultato dell'operazione richiesta dal chiamante, "
    graph = FileGraph(
        root="/repo",
        entities=[
            _edge(f"src/core.py::{comune}gestendo il caso vuoto.", "src/core.py::bersaglio", "rationale_for"),
            _edge(f"src/core.py::{comune}sollevando se manca il permesso.", "src/core.py::bersaglio", "rationale_for"),
        ],
    )

    result = explain_entity(graph, "bersaglio")

    assert len(result["rationale"]) == 2


def test_the_default_limit_is_a_named_constant() -> None:
    """Non un letterale nella firma.

    Un numero scelto a occhio ha diritto di esistere, ma deve stare in un solo
    posto e portare scritta la sua ragione, altrimenti nessuno sa se e' una
    soglia misurata o un gusto personale.
    """

    graph = FileGraph(
        root="/repo",
        entities=[_edge(f"src/m{i}.py::c{i}", "src/core.py::bersaglio") for i in range(EXPLAIN_GROUP_LIMIT + 3)],
    )

    assert len(explain_entity(graph, "bersaglio")["callers"]) == EXPLAIN_GROUP_LIMIT


# ── il filtro per estensione ──────────────────────────────────────────────

def test_the_suffix_filter_can_be_extended(monkeypatch) -> None:
    monkeypatch.setenv("TRUENEX_GRAPH_SUFFIXES", ".zig, ex")

    result = code_suffixes()

    assert ".zig" in result and ".ex" in result
    assert ".py" in result, "aggiungere non deve togliere"


def test_the_suffix_filter_can_be_replaced(monkeypatch) -> None:
    monkeypatch.setenv("TRUENEX_GRAPH_SUFFIXES", "=.py")

    result = code_suffixes()

    assert result == frozenset({".py"})


def test_without_the_variable_the_default_stands(monkeypatch) -> None:
    monkeypatch.delenv("TRUENEX_GRAPH_SUFFIXES", raising=False)

    assert code_suffixes() == DEFAULT_CODE_SUFFIXES


def test_what_the_filter_dropped_is_reported(tmp_path: Path) -> None:
    """Un file ignorato per estensione e' indistinguibile da uno senza relazioni.

    Se il grafo non dichiara di aver saltato 30 `.zig`, «chi chiama questa
    funzione zig» risponde «nessuno» — vero come frase, falso come risposta.
    """

    (tmp_path / "vero.py").write_text("x = 1\n", encoding="utf-8")
    for i in range(3):
        (tmp_path / f"ignorato{i}.zig").write_text("x = 1\n", encoding="utf-8")

    skipped: dict[str, int] = {}
    found = collect_source_files(tmp_path, skipped_out=skipped)

    assert [p.name for p in found] == ["vero.py"]
    assert skipped[".zig"] == 3


def test_the_graph_has_no_exclusion_policy_of_its_own() -> None:
    """Le cartelle da saltare si decidono in un posto solo.

    Qui c'e' stata una lista di cinque nomi tutti gia' presenti nel set
    condiviso: non cambiava niente, ma chi avesse modificato quello condiviso
    avrebbe trovato due fonti, una autorevole solo in apparenza.
    """

    from truenex_memory.graph import GRAPH_EXTRA_EXCLUDED_DIRS

    assert GRAPH_EXTRA_EXCLUDED_DIRS == frozenset(), (
        "se serve escludere una cartella si aggiunge in core/exclusions.py, "
        "dove la vedono anche l'indice e la GUI"
    )

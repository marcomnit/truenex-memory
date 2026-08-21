"""La porta strutturale: chi chiama cosa, letto dal grafo del codice.

Perche' e' un tool separato e non un contributo al ranking: il 2026-08-21 ogni
tentativo di aggiungere candidati alla classifica fusa ha perso casi — un
cross-encoder sull'unione dei candidati, un instradamento del ramo denso in tre
varianti, la potatura dei termini della query. Una porta nuova aggiunge una
capacita' senza toccare una classifica che e' costata una giornata a stabilizzare.

Il guadagno misurato per un agente: rispondere a «chi chiama questa funzione,
quali test la coprono, cosa chiama» costa 1.604 caratteri col tool contro
108.707 leggendo i quattro file coinvolti. Sessantotto volte meno.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.graph import EntityEdge, FileGraph, collect_entity_edges, explain_entity


def _edge(source: str, target: str, relation: str) -> EntityEdge:
    return EntityEdge(
        source=source,
        target=target,
        relation_type=relation,
        source_file=source.split("::")[0],
        target_file=target.split("::")[0],
    )


def _graph() -> FileGraph:
    """Un grafo minimo che riproduce le quattro forme che il tool distingue."""

    return FileGraph(
        root="/repo",
        entities=[
            _edge("src/app.py::run", "src/core.py::do_work", "calls"),
            _edge("src/core.py::do_work", "src/util.py::helper", "calls"),
            _edge("tests/test_core.py::test_do_work", "src/core.py::do_work", "calls"),
            _edge("src/core.py::core.py", "src/core.py::do_work", "contains"),
            _edge(
                "src/core.py::Esegue il lavoro e restituisce il risultato.",
                "src/core.py::do_work",
                "rationale_for",
            ),
        ],
    )


# ── collect_entity_edges ──────────────────────────────────────────────────

def test_entities_are_addressed_by_file_and_name() -> None:
    """Gli id dell'estrattore non vengono riusati: sono suoi e possono cambiare."""

    nodes = [
        {"id": "n1", "label": "run()", "source_file": "src/app.py"},
        {"id": "n2", "label": "do_work()", "source_file": "src/core.py"},
    ]
    edges = [{"source": "n1", "target": "n2", "relation": "calls"}]

    result = collect_entity_edges(nodes, edges, Path("/repo"))

    assert len(result) == 1
    assert result[0].source == "src/app.py::run"
    assert result[0].target == "src/core.py::do_work"


def test_intra_file_relations_are_kept_here() -> None:
    """Al contrario dell'aggregazione per file.

    Una funzione che chiama un'altra funzione dello stesso modulo e' esattamente
    cio' che vuole sapere chi chiede «cosa usa questa»; nell'aggregazione per
    file invece sarebbe rumore, perche' non dice niente su come e' legato il
    progetto.
    """

    nodes = [
        {"id": "a", "label": "uno()", "source_file": "src/same.py"},
        {"id": "b", "label": "due()", "source_file": "src/same.py"},
    ]

    result = collect_entity_edges(nodes, [{"source": "a", "target": "b", "relation": "calls"}], Path("/repo"))

    assert len(result) == 1


def test_nodes_without_a_label_or_a_file_are_dropped() -> None:
    nodes = [
        {"id": "a", "label": "uno()", "source_file": "src/x.py"},
        {"id": "b", "source_file": "src/y.py"},           # senza nome
        {"id": "c", "label": "tre()"},                     # senza file
    ]
    edges = [
        {"source": "a", "target": "b", "relation": "calls"},
        {"source": "a", "target": "c", "relation": "calls"},
    ]

    assert collect_entity_edges(nodes, edges, Path("/repo")) == []


def test_duplicate_relations_collapse() -> None:
    nodes = [
        {"id": "a", "label": "uno()", "source_file": "src/x.py"},
        {"id": "b", "label": "due()", "source_file": "src/y.py"},
    ]
    edges = [{"source": "a", "target": "b", "relation": "calls"}] * 3

    assert len(collect_entity_edges(nodes, edges, Path("/repo"))) == 1


# ── explain_entity ────────────────────────────────────────────────────────

def test_callers_calls_tests_and_rationale_are_separated() -> None:
    """Le quattro risposte sono gruppi distinti, non un elenco piatto.

    Un chiamante di produzione e un test non sono la stessa informazione, e il
    docstring che spiega la funzione non e' un chiamante affatto.
    """

    result = explain_entity(_graph(), "do_work")

    assert [c["entity"] for c in result["callers"]] == ["src/app.py::run"]
    assert [c["entity"] for c in result["calls"]] == ["src/util.py::helper"]
    assert [t["entity"] for t in result["tests"]] == ["tests/test_core.py::test_do_work"]
    assert result["rationale"] == ["Esegue il lavoro e restituisce il risultato."]


def test_containment_is_not_a_caller() -> None:
    """`contains` dice solo che la funzione sta in quel file."""

    result = explain_entity(_graph(), "do_work")

    assert not any("core.py::core.py" in c["entity"] for c in result["callers"])


def test_a_file_path_works_as_a_target() -> None:
    result = explain_entity(_graph(), "src/core.py")

    assert result["matched"], "un percorso deve corrispondere come un nome"


def test_matching_is_case_insensitive_and_separator_agnostic() -> None:
    assert explain_entity(_graph(), "SRC/CORE.PY")["matched"]
    assert explain_entity(_graph(), "src\\core.py")["matched"]


def test_an_unknown_target_answers_nothing_rather_than_guessing() -> None:
    """La differenza con la ricerca: qui l'assenza e' una risposta.

    Le relazioni sono lette dal codice, quindi o ci sono o non ci sono. Non
    esiste il caso "plausibile ma sbagliato" che affligge il recupero testuale.
    """

    result = explain_entity(_graph(), "funzione_che_non_esiste")

    assert result["matched"] == []
    assert result["callers"] == []
    assert result["tests"] == []


def test_empty_target_is_not_a_wildcard() -> None:
    result = explain_entity(_graph(), "   ")

    assert result["matched"] == []


def test_the_limit_caps_every_group() -> None:
    """Il tool alimenta un agente che paga a token."""

    many = FileGraph(
        root="/repo",
        entities=[
            _edge(f"tests/test_{i}.py::test_{i}", "src/core.py::do_work", "calls")
            for i in range(30)
        ],
    )

    assert len(explain_entity(many, "do_work", limit=5)["tests"]) == 5


# ── il tool MCP ───────────────────────────────────────────────────────────

def test_the_tool_reports_a_missing_graph_instead_of_failing(tmp_path: Path, monkeypatch) -> None:
    """Senza un grafo costruito il tool spiega cosa fare."""

    from truenex_memory.mcp.tools import memory_graph

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    result = memory_graph("qualsiasi", project_root=tmp_path)

    assert "error" in result
    assert "graph build" in result["hint"]

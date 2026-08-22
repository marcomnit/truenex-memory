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


# ── cio' che il grafo NON puo' garantire, detto nella risposta ────────────

def _grafo_rust() -> FileGraph:
    """Riproduce il caso misurato: definizione e test nello stesso file.

    Su `publish_vault_state` il grafo trovava solo l'impl e il test, entrambi in
    `hub_ipc.rs`, e mancavano i due chiamanti di produzione in
    `hub_connect.rs` — chiamate a metodo attraverso un ricevitore.
    """

    return FileGraph(
        root="/repo",
        entities=[
            _edge("src/hub_ipc.rs::AuthenticatedIpcSession<S>", "src/hub_ipc.rs::publish_vault_state", "method"),
            _edge("src/hub_ipc.rs::publisher_requires_a_receipt", "src/hub_ipc.rs::publish_vault_state"),
        ],
    )


def test_an_absence_is_not_presented_as_a_finding() -> None:
    """Il difetto peggiore possibile per questo strumento.

    Il tool dichiarava «esatto o assente, mai plausibile», e il profilo dice di
    preferirlo alla lettura dei file. Un agente che obbedisce risponde «lo chiama
    solo il test» — con sicurezza, e sbagliando. Misurato il 2026-08-22: 83% dei
    chiamanti cross-file mancanti su Rust (19 su 23).
    """

    risultato = explain_entity(_grafo_rust(), "publish_vault_state")

    copertura = risultato["coverage"]
    assert copertura["callers_outside_the_defining_file"] == 0
    # Codici compatti, non prose: la spiegazione sta nella descrizione del tool,
    # che il protocollo consegna una volta per sessione invece che a ogni
    # risposta. Il numero misurato resta nella risposta, perche' e' l'unica parte
    # che cambia.
    assert any("cross_file_method_calls" in a and "%" in a for a in copertura["incomplete"])
    assert any("no_caller_outside_defining_file" in a for a in copertura["incomplete"])


def test_an_empty_test_list_says_it_does_not_know() -> None:
    """In Rust i test stanno nello stesso file e non si chiamano «test».

    `publisher_requires_a_fully_correlated_receipt` e' un `#[test]` senza la
    parola «test» nel nome, in `hub_ipc.rs`: ne' il percorso ne' il nome lo
    tradiscono. Rispondere «nessun test» quando non si sa e' una bugia.
    """

    risultato = explain_entity(_grafo_rust(), "publish_vault_state")

    assert risultato["tests"] == []
    assert risultato["coverage"]["tests_detection"].startswith("unknown")


def test_a_language_with_reliable_extraction_gets_no_caveat() -> None:
    """L'avvertenza non e' un disclaimer generico appiccicato a tutto.

    Un avviso che compare sempre viene ignorato sempre: qui c'e' solo dove e'
    stato misurato un buco.
    """

    grafo = FileGraph(
        root="/repo",
        entities=[_edge("src/app.py::run", "src/core.py::do_work")],
    )

    copertura = explain_entity(grafo, "do_work")["coverage"]

    assert "incomplete" not in copertura
    assert "tests_detection" not in copertura


def test_cross_file_callers_suppress_the_shape_warning() -> None:
    """Se i chiamanti fuori dal file ci sono, quella firma non si applica.

    L'avvertenza sul linguaggio resta — il buco esiste comunque — ma non si
    aggiunge «nessun chiamante fuori dal file», che sarebbe falso.
    """

    grafo = FileGraph(
        root="/repo",
        entities=[
            _edge("src/hub_connect.rs::publish_loop", "src/hub_ipc.rs::publish_vault_state"),
        ],
    )

    copertura = explain_entity(grafo, "publish_vault_state")["coverage"]

    assert copertura["callers_outside_the_defining_file"] == 1
    assert not any("nessun chiamante fuori" in a for a in copertura["incomplete"])


def test_the_tool_description_no_longer_guarantees_completeness() -> None:
    """La descrizione arriva a ogni client: e' lì che la promessa contava."""

    from truenex_memory.mcp.server import _tool_definitions

    grafo = next(d for d in _tool_definitions() if d["name"] == "memory_graph")
    descrizione = grafo["description"].lower()

    assert "proves nothing" in descrizione or "not report proves" in descrizione
    assert "measured, not guessed" in descrizione, (
        "la descrizione spiega i codici; il numero misurato viaggia nella risposta"
    )
    assert "coverage" in descrizione


# ── rumore e omonimi ──────────────────────────────────────────────────────

def test_language_types_do_not_become_project_entities() -> None:
    """579 archi verso una finta entita' `String`: il 10,2% del grafo.

    L'estrattore promuove `String` a entita' e la attribuisce al primo file in
    cui la incontra; da lì ogni `String` di ogni file le si aggancia. Non e' un
    fastidio estetico: quel rumore occupa le prime posizioni di «cosa usa questa
    funzione» e spinge in basso le relazioni vere, che e' il modo piu' silenzioso
    di rendere inutile uno strumento.
    """

    from truenex_memory.graph import collect_entity_edges

    nodi = [
        {"id": "a", "label": "carica()", "source_file": "src/app.rs"},
        {"id": "s", "label": "String", "source_file": "src/license.rs"},
        {"id": "v", "label": "verifica()", "source_file": "src/license.rs"},
    ]
    archi = [
        {"source": "a", "target": "s", "relation": "references"},
        {"source": "a", "target": "v", "relation": "calls"},
    ]

    risultato = collect_entity_edges(nodi, archi, Path("/repo"))

    assert [e.target for e in risultato] == ["src/license.rs::verifica"]


def test_a_real_call_on_a_language_type_survives() -> None:
    """Si filtra `references`, non le chiamate: `String::from_utf8` e' reale.

    Un filtro piu' larghe avrebbe cancellato relazioni vere per togliere rumore,
    che e' un rimedio peggiore del male.
    """

    from truenex_memory.graph import collect_entity_edges

    nodi = [
        {"id": "a", "label": "carica()", "source_file": "src/app.rs"},
        {"id": "s", "label": "String", "source_file": "src/license.rs"},
    ]
    archi = [{"source": "a", "target": "s", "relation": "calls"}]

    assert len(collect_entity_edges(nodi, archi, Path("/repo"))) == 1


def test_an_exact_name_does_not_drag_in_its_longer_namesake() -> None:
    """I metodi sono registrati col punto davanti: `.verify_token`.

    Quindi il confronto esatto con `verify_token` falliva, si cadeva sulla
    sottostringa, e chi cercava un nome preciso riceveva anche
    `verify_token_for_device` senza modo di distinguerli.
    """

    grafo = FileGraph(
        root="/repo",
        entities=[
            _edge("src/lic.rs::chiamante", "src/lic.rs::.verify_token"),
            _edge("src/lic.rs::altro", "src/lic.rs::verify_token_for_device"),
        ],
    )

    risultato = explain_entity(grafo, "verify_token")

    assert risultato["matched"] == ["src/lic.rs::.verify_token"]


def test_a_partial_name_still_finds_candidates() -> None:
    """Restringere il confronto esatto non deve rompere la ricerca parziale.

    Chi non ricorda il nome intero deve ancora trovare qualcosa: senza match
    esatto vale la sottostringa, come prima.
    """

    grafo = FileGraph(
        root="/repo",
        entities=[_edge("src/lic.rs::altro", "src/lic.rs::verify_token_for_device")],
    )

    assert explain_entity(grafo, "verify_tok")["matched"]


# ── il ripiego testuale ───────────────────────────────────────────────────

def _albero_rust(tmp_path: Path) -> Path:
    """Riproduce il caso reale: definizione in un file, chiamanti in un altro."""

    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "hub_ipc.rs").write_text(
        "impl<S> Session<S> {\n"
        "    pub async fn publish_vault_state(&mut self) {}\n"
        "}\n"
        "#[test]\n"
        "fn publisher_requires_a_receipt() {\n"
        "    session.publish_vault_state();\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "hub_connect.rs").write_text(
        "async fn publish_loop() {\n"
        "    // qui NON e' una chiamata: publish_vault_state( in un commento\n"
        "    session.publish_vault_state(&id, true).await;\n"
        "    session.publish_vault_state(&id, false).await;\n"
        "}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_text_fallback_finds_what_the_parser_missed(tmp_path: Path) -> None:
    """I due chiamanti di produzione che il grafo perdeva.

    Non ripara il grafo: ripara la risposta. Il difetto misurato — 83% dei
    chiamanti cross-file mancanti su Rust — resta, ma smette di presentarsi come
    «nessun chiamante».
    """

    from truenex_memory.graph import text_call_sites

    radice = _albero_rust(tmp_path)

    trovati = text_call_sites(radice, "publish_vault_state", files=["src/hub_ipc.rs", "src/hub_connect.rs"])

    righe = {(t["file"], t["line"]) for t in trovati}
    assert ("src/hub_connect.rs", 3) in righe
    assert ("src/hub_connect.rs", 4) in righe


def test_comments_are_not_call_sites(tmp_path: Path) -> None:
    """Un candidato falso insegna a ignorare tutti i candidati."""

    from truenex_memory.graph import text_call_sites

    radice = _albero_rust(tmp_path)

    trovati = text_call_sites(radice, "publish_vault_state", files=["src/hub_connect.rs"])

    assert all("//" not in t["text"] for t in trovati)
    assert 2 not in {t["line"] for t in trovati}, "la riga di commento non e' una chiamata"


def test_the_definition_line_is_not_a_call_site(tmp_path: Path) -> None:
    from truenex_memory.graph import text_call_sites

    radice = _albero_rust(tmp_path)

    trovati = text_call_sites(radice, "publish_vault_state", files=["src/hub_ipc.rs"])

    assert 2 not in {t["line"] for t in trovati}, "`pub async fn nome(` e' la definizione"


def test_the_search_uses_the_graph_file_list_not_a_new_walk(tmp_path: Path) -> None:
    """Ripercorrere l'albero costava 1,5 s per interrogazione.

    A quel prezzo il ripiego verrebbe spento, cioe' non servirebbe a niente: i
    file su cui cercare li conosce gia' il grafo, e cosi' costa 20 ms.
    """

    from truenex_memory.graph import text_call_sites

    radice = _albero_rust(tmp_path)

    solo_uno = text_call_sites(radice, "publish_vault_state", files=["src/hub_ipc.rs"])

    assert all(t["file"] == "src/hub_ipc.rs" for t in solo_uno), (
        "cerca solo nei file indicati, non in tutto l'albero"
    )


def test_an_empty_target_searches_nothing(tmp_path: Path) -> None:
    from truenex_memory.graph import text_call_sites

    assert text_call_sites(tmp_path, "   ", files=[]) == []


def test_the_limit_stops_the_scan(tmp_path: Path) -> None:
    from truenex_memory.graph import text_call_sites

    radice = _albero_rust(tmp_path)

    trovati = text_call_sites(
        radice, "publish_vault_state", limit=1, files=["src/hub_connect.rs"]
    )

    assert len(trovati) == 1


def test_candidates_never_enter_the_callers_list() -> None:
    """Due qualita' di verita' nella stessa risposta, in due campi distinti.

    Sollevato da entrambe le review: mescolare una relazione letta dal parser
    con una riga di testo compatibile distruggerebbe la fiducia anche nelle
    relazioni vere. Il contratto per chi legge viaggia accanto ai dati.
    """

    from truenex_memory.mcp.server import _tool_definitions

    descrizione = next(
        d for d in _tool_definitions() if d["name"] == "memory_graph"
    )["description"].lower()

    assert "candidate_callers_from_text" in descrizione
    assert "never to be reported as graph-resolved" in descrizione

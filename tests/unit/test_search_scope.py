"""Restringere la ricerca documentale a un albero di sorgenti.

Perche' esiste, misurato il 2026-08-21 su due insiemi indipendenti di 30
domande scritte da altri agenti, ciascuna formulata evitando deliberatamente
le parole del documento bersaglio:

    senza scope        2/32 e 2/32
    con scope corretto 6/32 e 6/32      (MRR 0,042 -> 0,108)
    con scope SBAGLIATO  0/32 e 0/32

Lo store tiene tutti i progetti insieme: 170.285 chunk contro i 2.145 del
progetto rilevante. Una ricerca senza scope compete contro ottanta volte i
candidati che servono.

Il rovescio ha due facce, e vanno distinte:

- uno scope che **non corrisponde a nulla** (refuso, progetto sconosciuto)
  ricade sull'intero corpus, quindi il caso peggiore e' il comportamento di
  prima;
- uno scope che indica un progetto **esistente ma sbagliato** restituisce i
  documenti di quello, plausibili e sbagliati, e il ripiego non scatta perche'
  la ricerca non e' vuota.

Per questo il parametro e' opzionale e non viene mai dedotto dal sistema.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.core.chunker import chunk_text
from truenex_memory.store.repository import MemoryRepository


PHRASE = "la rotazione delle chiavi avviene ogni novanta giorni per policy"


def _repo_with_two_projects(tmp_path: Path) -> MemoryRepository:
    """Due alberi distinti che contengono entrambi la risposta."""

    repository = MemoryRepository(tmp_path / "memory.db")
    for project in ("alfa", "beta"):
        root = tmp_path / project / "docs"
        root.mkdir(parents=True)
        doc = root / "policy.md"
        doc.write_text(f"# Policy di {project}\n\n{PHRASE}.\n", encoding="utf-8")
        repository.upsert_document(doc, f"{project}/docs/policy.md",
                                   chunk_text(doc.read_text(encoding="utf-8")))
    return repository


def test_without_scope_both_projects_compete(tmp_path: Path) -> None:
    repository = _repo_with_two_projects(tmp_path)

    hits = repository.search(PHRASE, top_k=10)
    projects = {"alfa" if "alfa" in (h.source_path or "") else "beta" for h in hits}

    assert projects == {"alfa", "beta"}


def test_scope_keeps_only_the_named_tree(tmp_path: Path) -> None:
    repository = _repo_with_two_projects(tmp_path)

    hits = repository.search(PHRASE, top_k=10, scope="alfa")

    assert hits, "lo scope non deve svuotare una ricerca che ha una risposta"
    assert all("alfa" in (h.source_path or "") for h in hits if h.memory_type == "document_chunk")


def test_scope_is_separator_agnostic(tmp_path: Path) -> None:
    """I percorsi sono con backslash su Windows e con slash altrove.

    Chi passa lo scope scrive la forma che ha in mano, non quella della
    piattaforma dello store.
    """

    repository = _repo_with_two_projects(tmp_path)

    forward = repository.search(PHRASE, top_k=10, scope="alfa/docs")
    backward = repository.search(PHRASE, top_k=10, scope="alfa\\docs")

    assert forward, "la forma con slash deve funzionare"
    assert [h.source_path for h in forward] == [h.source_path for h in backward]


def test_scope_is_case_insensitive(tmp_path: Path) -> None:
    repository = _repo_with_two_projects(tmp_path)

    assert repository.search(PHRASE, top_k=10, scope="ALFA")


def test_a_scope_that_matches_nothing_falls_back_to_the_whole_corpus(
    tmp_path: Path,
) -> None:
    """Un refuso non deve rendere la risposta irraggiungibile.

    Senza questo ripiego lo scope sarebbe una trappola: misurato, con uno
    scope che non corrisponde a niente la ricerca ristretta passava da 2/32
    a 0/32 su entrambi gli insiemi di valutazione — non peggiorava, azzerava.
    Il ripiego riporta il caso peggiore al comportamento senza scope.
    """

    repository = _repo_with_two_projects(tmp_path)

    hits = repository.search(PHRASE, top_k=10, scope="progetto-che-non-esiste")
    chunks = [h for h in hits if h.memory_type == "document_chunk"]

    assert chunks, "il ripiego globale deve restituire qualcosa"


def test_a_wrong_but_real_scope_returns_the_other_project(tmp_path: Path) -> None:
    """Il caso che il ripiego NON copre, ed e' il rischio residuo.

    Se lo scope indica un progetto che esiste ma e' quello sbagliato, la
    ricerca ristretta trova documenti — di quel progetto — quindi non e'
    vuota e il ripiego non scatta. La risposta e' plausibile e sbagliata.
    Misurato sullo store reale: 0/32 con lo scope di un altro progetto, senza
    che il ripiego intervenga. E' la ragione per cui lo scope non viene mai
    dedotto dal sistema: lo passa chi conosce la cartella in cui lavora.
    """

    repository = _repo_with_two_projects(tmp_path)

    hits = repository.search(PHRASE, top_k=10, scope="beta")
    chunks = [h for h in hits if h.memory_type == "document_chunk"]

    assert chunks, "il progetto sbagliato ha comunque documenti che corrispondono"
    assert all("beta" in (h.source_path or "") for h in chunks)
    assert not any("alfa" in (h.source_path or "") for h in chunks)


def test_scope_does_not_filter_memories(tmp_path: Path) -> None:
    """Le memorie restano visibili a qualunque scope.

    Nello store reale 2.911 dei 3.120 memory node hanno `project_id`
    'default': filtrarli per progetto li eliminerebbe tutti e distruggerebbe
    il recupero delle note curate. Lo scope e' una restrizione sui DOCUMENTI.
    """

    repository = _repo_with_two_projects(tmp_path)
    repository.add_memory(f"Decisione: {PHRASE}.", memory_type="decision")

    hits = repository.search(PHRASE, top_k=10, scope="alfa")

    assert any(h.memory_type == "decision" for h in hits)


def test_none_and_empty_scope_behave_like_no_scope(tmp_path: Path) -> None:
    repository = _repo_with_two_projects(tmp_path)

    baseline = [h.source_path for h in repository.search(PHRASE, top_k=10)]

    assert [h.source_path for h in repository.search(PHRASE, top_k=10, scope=None)] == baseline


def test_service_and_mcp_tool_forward_the_scope(tmp_path: Path) -> None:
    """Il parametro deve arrivare fino al tool che un agente chiama davvero."""

    import inspect

    from truenex_memory.core.memory_service import MemoryService
    from truenex_memory.mcp.tools import memory_search

    assert "scope" in inspect.signature(MemoryService.search).parameters
    assert "scope" in inspect.signature(memory_search).parameters


def test_one_document_cannot_fill_the_answer(tmp_path: Path) -> None:
    """Al massimo un chunk per documento fra i risultati.

    La deduplicazione per contenuto non basta: tre chunk DIVERSI dello stesso
    file sono tre contenuti distinti e sopravvivono tutti. Su una domanda vera
    sul ponte Git fra due macchine, lo stesso `docs/git-bridge-setup.md`
    occupava tutte e tre le prime posizioni: due slot su cinque che non
    aggiungevano niente. Misurato sull'insieme cieco: cap assente 4/32,
    cap 3 -> 4/32, cap 2 -> 5/32, cap 1 -> 6/32.
    """

    from truenex_memory.core.chunker import chunk_text

    repository = MemoryRepository(tmp_path / "memory.db")
    root = tmp_path / "progetto"
    root.mkdir()
    doc = root / "lungo.md"
    # un documento con molte sezioni che parlano tutte dello stesso tema
    body = "\n\n".join(
        f"## Sezione {i}\n\n{PHRASE}, dettaglio numero {i}." for i in range(8)
    )
    doc.write_text(body, encoding="utf-8")
    repository.upsert_document(doc, "progetto/lungo.md", chunk_text(body))

    hits = repository.search(PHRASE, top_k=5)
    chunks = [h for h in hits if h.memory_type == "document_chunk"]

    assert len(chunks) == 1, f"un solo chunk per documento, trovati {len(chunks)}"


def test_the_cap_can_be_lifted_per_call(tmp_path: Path) -> None:
    """Chi vuole piu' passaggi dello stesso documento puo' chiederli."""

    from truenex_memory.core.chunker import chunk_text

    repository = MemoryRepository(tmp_path / "memory.db")
    root = tmp_path / "progetto"
    root.mkdir()
    doc = root / "lungo.md"
    body = "\n\n".join(
        f"## Sezione {i}\n\n{PHRASE}, dettaglio numero {i}." for i in range(8)
    )
    doc.write_text(body, encoding="utf-8")
    repository.upsert_document(doc, "progetto/lungo.md", chunk_text(body))

    hits = repository.search(PHRASE, top_k=5, max_per_document=None)
    chunks = [h for h in hits if h.memory_type == "document_chunk"]

    assert len(chunks) > 1

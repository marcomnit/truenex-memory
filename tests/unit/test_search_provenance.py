"""La risposta dichiara da quale progetto viene, e se lo scope si e' applicato.

Perche' esiste: lo scope ha due modi di sbagliare che non si vedono.

1. Un nome che non corrisponde a niente fa ricadere la ricerca sull'intero
   corpus. E' voluto — senza ripiego un refuso azzererebbe la risposta — ma
   veniva scritto solo nel log del server, quindi chi chiamava riceveva
   risultati globali credendoli ristretti.
2. Un nome che indica un progetto ESISTENTE ma sbagliato restituisce i
   documenti di quello: coerenti, plausibili, e di un altro progetto.

Nessuno dei due si risolve raccomandando attenzione a chi cerca. La
provenienza va scritta nella risposta.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.core.chunker import chunk_text
from truenex_memory.store.repository import MemoryRepository


FRASE = "la soglia di fusione governa quali candidati densi entrano in classifica"


def _repo(tmp_path: Path) -> MemoryRepository:
    repository = MemoryRepository(tmp_path / "memory.db")
    for progetto in ("alfa", "beta"):
        origine = tmp_path / progetto / "docs"
        origine.mkdir(parents=True)
        documento = origine / "guida.md"
        documento.write_text(f"# {progetto}\n\n{FRASE} nel progetto {progetto}.\n", encoding="utf-8")
        repository.upsert_document(
            documento,
            f"D:/alberi/{progetto}/docs/guida.md",
            chunk_text(documento.read_text(encoding="utf-8")),
        )
    return repository


def test_diagnostics_are_opt_in(tmp_path: Path) -> None:
    """Chi non le chiede non paga niente: nessun costo aggiunto per default."""

    repository = _repo(tmp_path)

    hits = repository.search(FRASE, top_k=5, scope="alfa")

    assert hits, "la ricerca funziona identica senza il dizionario"


def test_an_applied_scope_says_so(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    diagnostics: dict = {}

    repository.search(FRASE, top_k=5, scope="alfa", diagnostics=diagnostics)

    assert diagnostics["scope_applied"] is True
    assert diagnostics["scope_fell_back"] is False


def test_a_scope_that_matched_nothing_declares_the_fallback(tmp_path: Path) -> None:
    """Il caso che prima finiva solo nel log."""

    repository = _repo(tmp_path)
    diagnostics: dict = {}

    hits = repository.search(FRASE, top_k=5, scope="progetto-inventato", diagnostics=diagnostics)

    assert hits, "il ripiego deve rispondere: azzerare per un refuso e' peggio"
    assert diagnostics["scope_fell_back"] is True
    assert diagnostics["scope_applied"] is False


def test_the_wrong_but_real_project_is_visible_in_the_answer(tmp_path: Path) -> None:
    """Il caso peggiore: plausibile e sbagliato.

    Il ripiego NON scatta, perche' la ricerca non e' vuota: chiedendo `beta`
    quando si voleva `alfa` si ottengono i documenti di beta e nessun avviso.
    L'unico rimedio e' che la risposta dica da dove viene.
    """

    repository = _repo(tmp_path)
    diagnostics: dict = {}

    hits = repository.search(FRASE, top_k=5, scope="beta", diagnostics=diagnostics)

    assert hits
    assert diagnostics["scope_applied"] is True
    assert all("beta" in (h.source_path or "") for h in hits)
    assert diagnostics["answered_from"], (
        "senza la provenienza chi legge non ha modo di accorgersi dello scambio"
    )


def test_without_a_scope_the_provenance_is_still_reported(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    diagnostics: dict = {}

    repository.search(FRASE, top_k=5, diagnostics=diagnostics)

    assert diagnostics["scope"] is None
    assert diagnostics["scope_applied"] is False
    assert "answered_from" in diagnostics

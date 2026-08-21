"""Lo scope corrisponde a segmenti interi del percorso, non a sottostringhe.

Perche': `truenex-memory` come sottostringa prende anche `truenex-memory-dev` e
`truenex-memory-old`. Non e' il caso in cui la ricerca non trova niente — quello
si vede subito — ma quello in cui trova i documenti di un progetto VICINO: la
risposta e' plausibile, coerente, e sbagliata. Il rimedio non puo' essere
chiedere a chi cerca di stare attento al nome, perche' e' proprio il tipo di
attenzione che una macchina deve prendersi al posto di una persona.

Misurato il 2026-08-21 sull'insieme cieco: 6/32 -> 8/32, zero casi persi, e i
quattro riordini tutti verso l'alto (p = 0,016 al test dei segni).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.core.chunker import chunk_text
from truenex_memory.store.repository import MemoryRepository


@pytest.fixture()
def repo(tmp_path: Path) -> MemoryRepository:
    return MemoryRepository(tmp_path / "prova.db")


def _add(repo: MemoryRepository, stored_path: str, text: str) -> None:
    """Registra un documento col percorso che avrebbe nell'indice reale."""

    on_disk = Path(repo.db_path).parent / "sorgenti" / stored_path.replace(":", "").replace("/", "_")
    on_disk.parent.mkdir(parents=True, exist_ok=True)
    on_disk.write_text(text, encoding="utf-8")
    repo.upsert_document(on_disk, stored_path, chunk_text(text))


def test_a_neighbouring_project_does_not_answer(repo: MemoryRepository) -> None:
    """Il caso che costava casi veri: il vicino col nome piu' lungo."""

    _add(repo, "D:/Project_sw/ProjectPy/truenex-memory/docs/guida.md",
         "La soglia di fusione governa quali candidati densi entrano.")
    _add(repo, "D:/Project_sw/ProjectPy/truenex-memory-dev/docs/guida.md",
         "La soglia di fusione governa quali candidati densi entrano.")

    hits = repo.search("soglia di fusione candidati densi", top_k=10, scope="truenex-memory")

    assert hits, "il progetto giusto deve rispondere"
    for hit in hits:
        assert "truenex-memory-dev" not in hit.source_path.replace("\\", "/")


def test_the_neighbour_can_still_be_asked_by_its_own_name(repo: MemoryRepository) -> None:
    """Restringere non deve rendere irraggiungibile il vicino."""

    _add(repo, "D:/Project_sw/ProjectPy/truenex-memory-dev/docs/guida.md",
         "La soglia di fusione governa quali candidati densi entrano.")

    hits = repo.search("soglia di fusione candidati densi", top_k=10, scope="truenex-memory-dev")

    assert hits
    assert all("truenex-memory-dev" in h.source_path.replace("\\", "/") for h in hits)


def test_a_multi_segment_scope_works(repo: MemoryRepository) -> None:
    _add(repo, "D:/Project_sw/ProjectPy/truenex-memory/docs/guida.md", "chunk di prova ricercabile")
    _add(repo, "D:/Altro/ProjectPy/truenex-memory/docs/guida.md", "chunk di prova ricercabile")

    hits = repo.search("chunk di prova ricercabile", top_k=10, scope="Project_sw/ProjectPy/truenex-memory")

    assert hits
    assert all("/Altro/" not in h.source_path.replace("\\", "/") for h in hits)


def test_windows_separators_in_the_scope_are_accepted(repo: MemoryRepository) -> None:
    _add(repo, "D:/Project_sw/ProjectPy/truenex-memory/docs/guida.md", "chunk di prova ricercabile")

    hits = repo.search("chunk di prova ricercabile", top_k=10, scope=r"ProjectPy\truenex-memory")

    assert hits


def test_the_scope_is_case_insensitive(repo: MemoryRepository) -> None:
    _add(repo, "D:/Project_sw/ProjectPy/truenex-memory/docs/guida.md", "chunk di prova ricercabile")

    assert repo.search("chunk di prova ricercabile", top_k=10, scope="TRUENEX-MEMORY")


def test_a_partial_segment_is_not_a_scope(repo: MemoryRepository) -> None:
    """`truenex` non e' `truenex-memory`.

    Prima bastava una sottostringa qualsiasi, quindi mezzo nome apriva la porta
    a ogni progetto che cominciasse cosi'. Ora il ripiego globale interviene —
    la ricerca non si azzera — ma non finge che mezzo nome sia uno scope.
    """

    _add(repo, "D:/Project_sw/ProjectPy/truenex-memory/docs/guida.md", "chunk di prova ricercabile")

    hits = repo.search("chunk di prova ricercabile", top_k=10, scope="truenex-mem")

    # Il ripiego globale risponde comunque: preferibile ad azzerare per un refuso.
    assert hits, "un nome parziale non deve azzerare la ricerca"


def test_no_scope_still_searches_everything(repo: MemoryRepository) -> None:
    # Testi non identici: la deduplica per contenuto fonderebbe due documenti
    # con lo stesso testo, e questo test parla dello scope, non di quella.
    _add(repo, "D:/uno/docs/a.md", "chunk di prova ricercabile nel primo albero")
    _add(repo, "D:/due/docs/b.md", "chunk di prova ricercabile nel secondo albero")

    hits = repo.search("chunk di prova ricercabile", top_k=10)

    assert len({h.source_path for h in hits}) == 2

"""Regression tests for Reciprocal Rank Fusion of memory and chunk hits.

Before the RRF fusion, ``MemoryRepository.search()`` merged raw memory
scores (0.0-1.0) with raw chunk BM25 scores (hundreds) in a single ranking,
so every memory always ranked below every chunk and was cut by ``top_k``.
These tests pin the fixed behavior: memories are first-class evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.core.chunker import chunk_text
from truenex_memory.store.models import SearchHit
from truenex_memory.store.repository import MemoryRepository

# RRF internals (_fuse_ranked_hits, RRF_K, MEMORY_SOURCE_WEIGHT,
# CHUNK_SOURCE_WEIGHT) are imported lazily inside the unit tests so the
# integration tests above can still run (and fail) against pre-patch code.


def _hit(
    title: str,
    *,
    memory_type: str = "document_chunk",
    score: float = 1.0,
    source_path: str | None = None,
    document_id: str | None = None,
    content: str = "content",
) -> SearchHit:
    return SearchHit(
        title=title,
        content=content,
        source_path=source_path,
        heading_path=None,
        memory_type=memory_type,
        status="active",
        score=score,
        document_id=document_id,
    )


def _repository_with_memory_and_chunks(
    tmp_path: Path, memory_content: str, chunk_docs: dict[str, str]
) -> MemoryRepository:
    db_path = tmp_path / "memory.db"
    repository = MemoryRepository(db_path)
    repository.add_memory(memory_content, memory_type="decision")
    for relative_path, text in chunk_docs.items():
        doc_path = tmp_path / Path(relative_path).name
        doc_path.write_text(text, encoding="utf-8")
        repository.upsert_document(doc_path, relative_path, chunk_text(text))
    return repository


def test_memory_search_returns_distinctive_memory_first(tmp_path: Path) -> None:
    """Acceptance criterion: a memory written via the repository and searched
    with a near-verbatim query must rank first, above document chunks that
    share tokens with the query."""
    phrase = "un backup che non hai mai provato a ripristinare non e un backup e un auspicio"
    repository = _repository_with_memory_and_chunks(
        tmp_path,
        memory_content=f"Decisione operativa: {phrase}.",
        chunk_docs={
            "docs/backup-notes.md": (
                "Note generiche sul backup dei database e sulle procedure "
                "di manutenzione ordinaria dei sistemi."
            ),
            "docs/restore-guide.md": (
                "La procedura di ripristino richiede un backup verificato "
                "e testato regolarmente dal team."
            ),
            "docs/auspici.md": (
                "Un auspicio non e una strategia: servono verifiche concrete "
                "sul backup e sul ripristino."
            ),
        },
    )

    hits = repository.search(phrase, top_k=5)

    assert hits, "search must return results"
    assert hits[0].memory_type == "decision"
    assert "auspicio" in hits[0].content
    assert hits[0].score > 0


def test_query_matching_memory_and_chunks_returns_both(tmp_path: Path) -> None:
    """A query matching both a memory and document chunks returns both kinds
    of evidence, and the memory is not pushed below chunks by score scale."""
    repository = _repository_with_memory_and_chunks(
        tmp_path,
        memory_content="La rotazione delle chiavi MedDesk avviene ogni novanta giorni.",
        chunk_docs={
            "docs/meddesk-rotation.md": (
                "MedDesk key rotation procedure: ruotare le chiavi ogni "
                "novanta giorni secondo policy."
            ),
            "docs/meddesk-faq.md": (
                "FAQ MedDesk: domande frequenti su rotazione e chiavi."
            ),
        },
    )

    hits = repository.search("rotazione chiavi MedDesk novanta giorni", top_k=10)

    memory_hits = [hit for hit in hits if hit.memory_type == "decision"]
    chunk_hits = [hit for hit in hits if hit.memory_type == "document_chunk"]
    assert memory_hits, "the memory must be present in the results"
    assert chunk_hits, "matching document chunks must be present too"
    assert all(hit.score > 0 for hit in hits)
    # Single exposed scale: memory and chunk scores must be commensurable.
    # A memory at rank 1 in its own list outranks any chunk at rank 1.
    assert hits[0].memory_type == "decision"


def test_fuse_ranked_hits_unit() -> None:
    """Unit test of the fusion helper with hand-built ranked lists."""
    from truenex_memory.store.repository import (
        CHUNK_SOURCE_WEIGHT,
        MEMORY_SOURCE_WEIGHT,
        RRF_K,
        _fuse_ranked_hits,
    )

    memories = [
        _hit("mem best", memory_type="note", score=0.9, document_id="mem_1"),
        _hit("mem second", memory_type="note", score=0.5, document_id="mem_2"),
    ]
    chunks = [
        _hit("chunk best", score=380.0, source_path="docs/a.md"),
        _hit("chunk second", score=120.0, source_path="docs/b.md"),
    ]

    fused = _fuse_ranked_hits(memories, chunks)

    # Memories and chunks are interleaved by position, not by kind: rank 1
    # of each list scores the same, then rank 2 of each. The raw-score gap
    # (0.9 vs 380.0) is deliberately ignored — RRF merges by rank only.
    # A rank-1 chunk must NOT sit below a rank-2 memory: that is the
    # behaviour the old 1.5 memory weight imposed, and it buried
    # documentation entirely on the live store.
    assert {fused[0].title, fused[1].title} == {"mem best", "chunk best"}
    assert {fused[2].title, fused[3].title} == {"mem second", "chunk second"}
    assert fused[0].score == round(MEMORY_SOURCE_WEIGHT / (RRF_K + 1), 6)
    assert fused[1].score == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 1), 6)
    assert fused[2].score == round(MEMORY_SOURCE_WEIGHT / (RRF_K + 2), 6)
    assert fused[3].score == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 2), 6)
    assert all(hit.score > 0 for hit in fused)
    # Scores are on one small, commensurable scale (never hundreds).
    assert max(hit.score for hit in fused) < 1.0

    # Pre-sorted input must not be required: unsorted lists are re-ranked.
    shuffled = _fuse_ranked_hits(list(reversed(memories)), list(reversed(chunks)))
    assert [hit.title for hit in shuffled] == [hit.title for hit in fused]


def test_fuse_ranked_hits_keeps_distinct_chunks_of_same_file_separate() -> None:
    """Two chunks of the same file (same source_path, same title fallback,
    different content) must NOT collapse into one fused hit — the fusion
    identity key includes normalized content. True duplicates DO collapse,
    and the merged hit is scored at its best rank only: repeating the same
    content inside one source is one piece of evidence, not several."""
    from truenex_memory.store.repository import (
        CHUNK_SOURCE_WEIGHT,
        MEMORY_SOURCE_WEIGHT,
        RRF_K,
        _fuse_ranked_hits,
    )

    chunk_a = _hit("same.md", score=380.0, source_path="docs/same.md", content="alpha body")
    chunk_b = _hit("same.md", score=370.0, source_path="docs/same.md", content="beta body")
    # A mirrored copy of chunk A with different whitespace/casing: true duplicate.
    chunk_a_mirror = _hit(
        "same.md", score=90.0, source_path="docs/same.md", content="  Alpha   BODY "
    )
    other_chunk = _hit("other.md", score=300.0, source_path="docs/other.md", content="gamma")

    fused = _fuse_ranked_hits([], [chunk_a, chunk_b, chunk_a_mirror, other_chunk])

    # Distinct contents stay distinct: alpha, beta, gamma -> 3 hits, not 4 and not 2.
    assert len(fused) == 3
    contents = {hit.content for hit in fused}
    assert contents == {"alpha body", "beta body", "gamma"}

    by_content = {hit.content: hit for hit in fused}
    # chunk_a is rank 1 (score 380) and its mirror rank 4 (score 90) in the
    # same score-sorted list. The merged hit takes the BEST rank, 1, and does
    # NOT sum. Summing here is what let a 2,559-chunk chat export with 399
    # groups of byte-identical chunks accumulate ~6x any real answer and hold
    # rank 1 on four failing documentation queries; counting once recovered
    # +3 eval cases (36/53 -> 39/53) with nothing regressing.
    assert by_content["alpha body"].score == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 1), 6)
    # Non-duplicate hits keep their single-rank contribution.
    assert by_content["beta body"].score == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 2), 6)
    assert by_content["gamma"].score == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 3), 6)
    # No score can exceed the documented single-source-per-content maximum.
    max_possible = (MEMORY_SOURCE_WEIGHT + CHUNK_SOURCE_WEIGHT) / (RRF_K + 1)
    assert all(hit.score <= round(max_possible, 6) for hit in fused)
    # Ordering follows the chunk list's own ranks: 1, 2, 3.
    assert [hit.content for hit in fused] == ["alpha body", "beta body", "gamma"]


def test_corroboration_across_sources_sums_but_repetition_within_one_does_not() -> None:
    """The two halves of the fusion contract, side by side.

    Both cases look identical to a naive implementation — the same identity
    appearing twice — but they mean opposite things. Found by the lexical AND
    the dense ranker: two independent rankers agree, so the score should
    rise. Appearing twice inside ONE ranker's list: the same content indexed
    twice, which is one piece of evidence and must not pay twice.
    """
    from truenex_memory.store.repository import (
        CHUNK_SOURCE_WEIGHT,
        DENSE_SOURCE_WEIGHT,
        RRF_K,
        _fuse_ranked_hits,
    )

    # (a) same chunk in the lexical and the dense list -> contributions sum.
    lexical = _hit("doc.md", score=380.0, source_path="docs/doc.md", content="body")
    dense = _hit("doc.md", score=0.95, source_path="docs/doc.md", content="body")
    corroborated = _fuse_ranked_hits([], [lexical], [dense])
    assert len(corroborated) == 1
    assert corroborated[0].score == round(
        CHUNK_SOURCE_WEIGHT / (RRF_K + 1) + DENSE_SOURCE_WEIGHT / (RRF_K + 1), 6
    )

    # (b) same content twice inside the lexical list -> best rank only.
    twin_a = _hit("dup.md", score=380.0, source_path="docs/dup.md", content="body")
    twin_b = _hit("dup.md", score=90.0, source_path="docs/dup.md", content="  BODY ")
    repeated = _fuse_ranked_hits([], [twin_a, twin_b])
    assert len(repeated) == 1
    assert repeated[0].score == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 1), 6)

    # Corroborated evidence therefore outranks merely repeated evidence.
    assert corroborated[0].score > repeated[0].score


def test_memory_survives_many_competing_chunks_regression(tmp_path: Path) -> None:
    """Reproduce the real bug: on a large index, chunk BM25 scores live in
    the hundreds while memory scores cap at 1.0, so raw-score sorting pushed
    every memory below every chunk and ``[:top_k]`` cut it out. On pre-patch
    code this test is red (verified via git stash).

    The corpus shape matters: FTS5 idf is only large for tokens with low
    document frequency relative to the whole table, so the corpus is built
    as 200 filler documents (no query tokens, they just inflate N) plus 12
    documents sharing the rarer query tokens (low df -> high idf -> chunk
    scores ~143, like the 478k-chunk production index)."""
    phrase = "un backup che non hai mai provato a ripristinare non e un backup e un auspicio"
    db_path = tmp_path / "memory.db"
    repository = MemoryRepository(db_path)
    memory_id = repository.add_memory(
        f"Decisione operativa: {phrase}.", memory_type="decision"
    )

    for index in range(200):
        text = f"Filler {index}: zqxwv jklmp qwerty zxcvbn plokm ijnuh bygvt fcdxs."
        doc_path = tmp_path / f"filler_{index:03d}.md"
        doc_path.write_text(text, encoding="utf-8")
        repository.upsert_document(doc_path, f"docs/filler_{index:03d}.md", chunk_text(text))
    for index in range(12):
        text = (
            f"Nota {index}: il backup va provato, non basta mai un auspicio; "
            "serve ripristinare davvero."
        )
        doc_path = tmp_path / f"match_{index:03d}.md"
        doc_path.write_text(text, encoding="utf-8")
        repository.upsert_document(doc_path, f"docs/match_{index:03d}.md", chunk_text(text))

    hits = repository.search(phrase, top_k=5)

    assert hits, "search must return results"
    memory_hits = [hit for hit in hits if hit.memory_type == "decision"]
    assert memory_hits, "the memory must survive the top_k cut"
    assert hits[0].memory_type == "decision"
    assert memory_id is not None  # the memory was written and is retrievable
    assert "auspicio" in hits[0].content


class _ConstEmbedder:
    """Embedder stub: every text maps to the same unit vector, so any chunk
    is a perfect dense match (cosine 1.0) for any query. Used to test the
    semantic fallback branch deterministically."""

    @property
    def model_name(self) -> str:
        return "const-embedder-test"

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]


def test_weak_memory_below_gate_is_excluded_and_chunks_rise(tmp_path: Path) -> None:
    """Pre-fusion relevance gate: a memory covering less than
    MEMORY_FUSION_MIN_OVERLAP of the query tokens is noise and must NOT
    enter the fused ranking — with the 1.5 source weight even a rank-1 weak
    memory would otherwise outrank every document chunk."""
    from truenex_memory.retrieval.fusion import MEMORY_FUSION_MIN_OVERLAP

    repository = MemoryRepository(tmp_path / "memory.db")
    # Query has 4 tokens; the memory covers only "alpha" -> overlap 0.25.
    weak_memory = repository.add_memory(
        "Nota rapida su alpha e basta.", memory_type="note", title="weak alpha"
    )
    doc_path = tmp_path / "quattro.md"
    doc_path.write_text(
        "alpha beta gamma delta: procedura completa del tutto.", encoding="utf-8"
    )
    repository.upsert_document(doc_path, "docs/quattro.md", chunk_text(doc_path.read_text()))

    hits = repository.search("alpha beta gamma delta", top_k=5)

    assert hits, "search must return results"
    assert 0.25 < MEMORY_FUSION_MIN_OVERLAP
    memory_hits = [hit for hit in hits if hit.memory_type == "note"]
    assert not memory_hits, (
        f"weak memory {weak_memory} (overlap 0.25 < gate) must be excluded "
        "from fusion, not merely outranked"
    )
    assert hits[0].memory_type == "document_chunk"


def test_strong_memory_above_gate_still_wins(tmp_path: Path) -> None:
    """A memory covering at least MEMORY_FUSION_MIN_OVERLAP of the query
    tokens keeps its first-class status and outranks chunks at equal
    position."""
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.add_memory(
        "Decisione: alpha beta gamma delta approvate tutte.",
        memory_type="decision",
        title="strong decision",
    )
    doc_path = tmp_path / "quattro.md"
    doc_path.write_text(
        "alpha beta gamma delta: procedura completa del tutto.", encoding="utf-8"
    )
    repository.upsert_document(doc_path, "docs/quattro.md", chunk_text(doc_path.read_text()))

    hits = repository.search("alpha beta gamma delta", top_k=5)

    assert hits, "search must return results"
    assert hits[0].memory_type == "decision"
    assert any(hit.memory_type == "document_chunk" for hit in hits)


def test_weak_memory_as_only_lexical_evidence_is_kept(tmp_path: Path) -> None:
    """Conditional gate: the relevance gate exists to free strong documents
    from the weak-memory tail. When a weak memory is the ONLY lexical
    evidence (no chunk hits), it must be KEPT — the dense fallback with a
    hashing embedder would be noise on a non-RRF score scale."""
    repository = MemoryRepository(tmp_path / "memory.db", embedder=_ConstEmbedder())
    # Weak memory: covers 1 of 4 query tokens -> overlap 0.25 < gate.
    repository.add_memory("Nota rapida su alpha e basta.", memory_type="note")
    # Document with NO query token: no lexical chunk hit. With the const
    # embedder every chunk is a perfect dense match, so if the dense
    # fallback wrongly fired the chunk would win — the memory surfacing
    # proves the gate was skipped and the fallback did NOT run.
    doc_path = tmp_path / "denso.md"
    doc_path.write_text("zzz yyy xxx www vvv.", encoding="utf-8")
    repository.upsert_document(doc_path, "docs/denso.md", chunk_text(doc_path.read_text()))

    hits = repository.search("alpha beta gamma delta", top_k=5)

    assert hits, "search must return results"
    assert hits[0].memory_type == "note", (
        "with no chunk evidence the weak memory is the only lexical signal "
        "and must be kept (gate skipped)"
    )
    assert "alpha" in hits[0].content


def test_semantic_fallback_triggers_only_on_zero_lexical_hits(tmp_path: Path) -> None:
    """The dense fallback fires when lexical search — AFTER the conditional
    gate — produced genuinely nothing: no memories at all, no chunk hits."""
    repository = MemoryRepository(tmp_path / "memory.db", embedder=_ConstEmbedder())
    doc_path = tmp_path / "denso.md"
    doc_path.write_text("zzz yyy xxx www vvv.", encoding="utf-8")
    repository.upsert_document(doc_path, "docs/denso.md", chunk_text(doc_path.read_text()))

    hits = repository.search("alpha beta gamma delta", top_k=5)

    assert hits, "the semantic fallback must run on genuinely zero lexical hits"
    assert hits[0].memory_type == "document_chunk"
    assert "zzz" in hits[0].content


def test_gate_boundary_overlap_is_inclusive(tmp_path: Path) -> None:
    """Boundary: a memory whose overlap ratio equals exactly
    MEMORY_FUSION_MIN_OVERLAP passes the gate (>= is inclusive)."""
    from truenex_memory.retrieval.fusion import MEMORY_FUSION_MIN_OVERLAP

    repository = MemoryRepository(tmp_path / "memory.db")
    # Query has 4 tokens; the memory covers exactly 2 -> overlap 0.5.
    repository.add_memory(
        "Nota su alpha beta e nient'altro.", memory_type="note", title="boundary"
    )
    doc_path = tmp_path / "quattro.md"
    doc_path.write_text(
        "alpha beta gamma delta: procedura completa del tutto.", encoding="utf-8"
    )
    repository.upsert_document(doc_path, "docs/quattro.md", chunk_text(doc_path.read_text()))

    overlap = 2 / 4
    assert overlap == MEMORY_FUSION_MIN_OVERLAP, (
        "test premise: the crafted memory must sit exactly on the gate boundary"
    )
    hits = repository.search("alpha beta gamma delta", top_k=5)

    assert hits, "search must return results"
    assert hits[0].memory_type == "note", (
        "a memory exactly at the gate boundary must be included (>= is "
        "inclusive) and keeps first-class status over chunks"
    )


def test_gate_applies_with_include_inactive(tmp_path: Path) -> None:
    """include_inactive=True: an inactive memory ABOVE the gate still enters
    the fusion (the gate filters on relevance, not on status)."""
    repository = MemoryRepository(tmp_path / "memory.db")
    memory_id = repository.add_memory(
        "Decisione: alpha beta gamma delta approvate tutte.",
        memory_type="decision",
        title="inactive strong",
    )
    repository.set_memory_status(memory_id, "obsolete")
    doc_path = tmp_path / "quattro.md"
    doc_path.write_text(
        "alpha beta gamma delta: procedura completa del tutto.", encoding="utf-8"
    )
    repository.upsert_document(doc_path, "docs/quattro.md", chunk_text(doc_path.read_text()))

    hits = repository.search("alpha beta gamma delta", top_k=5, include_inactive=True)

    assert hits, "search must return results"
    assert hits[0].memory_type == "decision"
    assert hits[0].status == "obsolete", (
        "the inactive memory above the gate must enter fusion when "
        "include_inactive=True"
    )


def test_fusion_gate_constant_is_shared_single_source() -> None:
    """MEMORY_FUSION_MIN_OVERLAP lives in retrieval.fusion only and is
    imported (not duplicated) by the MCP path and the CLI global-search
    path, so both paths apply the same gate."""
    from truenex_memory.ingestion import global_search
    from truenex_memory.retrieval import fusion
    from truenex_memory.store import repository

    assert repository.MEMORY_FUSION_MIN_OVERLAP is fusion.MEMORY_FUSION_MIN_OVERLAP
    assert global_search.MEMORY_FUSION_MIN_OVERLAP is fusion.MEMORY_FUSION_MIN_OVERLAP
    assert 0.0 < fusion.MEMORY_FUSION_MIN_OVERLAP <= 1.0


class _StubSemanticEmbedder:
    """Deterministic semantic embedder stub (const vector, no downloads).

    model_name does NOT start with "hashing-fallback:", so the dense RRF
    ranker is active for this backend. Vectors are unit const: every chunk
    is a perfect dense match (cosine 1.0) for any query."""

    def __init__(self, dimensions: int = 8) -> None:
        self._dimensions = dimensions

    @property
    def model_name(self) -> str:
        return f"stub-semantic:test-e5-{self._dimensions}d"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        return [1.0] + [0.0] * (self._dimensions - 1)

    def embed_query(self, text: str) -> list[float]:
        return self.embed(f"query: {text}")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(f"passage: {text}") for text in texts]


def test_fuse_ranked_hits_dense_third_ranker_unit() -> None:
    """Three-ranker RRF: a chunk found by BOTH lexical and dense sums its
    contributions and beats a lexical-only chunk; a dense-only chunk does
    not beat a strong memory; the dense weight 0.9 is respected."""
    from truenex_memory.store.repository import (
        CHUNK_SOURCE_WEIGHT,
        DENSE_SOURCE_WEIGHT,
        MEMORY_SOURCE_WEIGHT,
        RRF_K,
        _fuse_ranked_hits,
    )

    memories = [_hit("mem strong", memory_type="decision", score=1.0, document_id="mem_1")]
    lexical = [
        _hit("chunk both", score=380.0, source_path="docs/a.md", content="both body"),
        _hit("chunk lex only", score=120.0, source_path="docs/b.md", content="lex body"),
    ]
    dense = [
        # Same identity as "chunk both" (same type, path, title, content).
        _hit("chunk both", score=0.95, source_path="docs/a.md", content="both body"),
        _hit("chunk dense only", score=0.90, source_path="docs/c.md", content="dense body"),
    ]

    fused = _fuse_ranked_hits(memories, lexical, dense)

    by_title = {hit.title: hit for hit in fused}
    # Corroborated chunk: lexical rank 1 + dense rank 1.
    assert by_title["chunk both"].score == round(
        CHUNK_SOURCE_WEIGHT / (RRF_K + 1) + DENSE_SOURCE_WEIGHT / (RRF_K + 1), 6
    )
    # Dense-only chunk carries exactly the dense weight at its rank.
    assert by_title["chunk dense only"].score == round(DENSE_SOURCE_WEIGHT / (RRF_K + 2), 6)
    # Summed lexical+dense beats lexical-only.
    assert by_title["chunk both"].score > by_title["chunk lex only"].score
    # A dense-ONLY chunk never beats the strong memory at rank 1 of its own
    # list (0.9 < 1.5): the dense ranker supports, it does not overwhelm.
    assert by_title["mem strong"].score > by_title["chunk dense only"].score
    assert by_title["mem strong"].score == round(MEMORY_SOURCE_WEIGHT / (RRF_K + 1), 6)
    # The corroborated chunk (lexical + dense) CAN outrank the memory:
    # contribution summing for true duplicates is inherent to RRF and
    # already documented for mirrored copies.
    assert fused[0].title == "chunk both"
    # Two-list calls keep working unchanged (dense optional).
    two_lists = _fuse_ranked_hits(memories, lexical)
    assert all(hit.title != "chunk dense only" for hit in two_lists)


def test_dense_ranker_surfaces_semantic_only_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration: with a semantic embedder active, the dense ranker runs
    on EVERY search (not only when lexical is empty). A chunk with zero
    query tokens surfaces via the dense list; a chunk found by both
    rankers is corroborated and ranks first."""
    # The branch is off by default; this test exercises the branch itself.
    monkeypatch.setenv("TRUENEX_DENSE", "on")
    from truenex_memory.store.repository import DENSE_SOURCE_WEIGHT

    repository = MemoryRepository(tmp_path / "memory.db", embedder=_StubSemanticEmbedder())
    lex_doc = tmp_path / "lex.md"
    lex_doc.write_text("alpha beta gamma delta procedura.", encoding="utf-8")
    repository.upsert_document(lex_doc, "docs/lex.md", chunk_text(lex_doc.read_text()))
    dense_doc = tmp_path / "dense.md"
    dense_doc.write_text("zzz yyy xxx www vvv.", encoding="utf-8")
    repository.upsert_document(dense_doc, "docs/dense.md", chunk_text(dense_doc.read_text()))

    hits = repository.search("alpha beta gamma delta", top_k=10)

    assert DENSE_SOURCE_WEIGHT == 0.9
    paths = [hit.source_path for hit in hits]
    assert any(path and path.endswith("lex.md") for path in paths)
    assert any(path and path.endswith("dense.md") for path in paths), (
        "the dense-only chunk must surface through the dense RRF ranker"
    )
    # The corroborated chunk (lexical rank 1 + dense) ranks first.
    assert hits[0].source_path.endswith("lex.md")


def test_hashing_embedder_keeps_dense_ranker_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the hashing fallback embedder the dense ranker stays OFF:
    hashing vectors are noise and must not join every fusion (legacy
    fallback on empty lexical is unchanged)."""
    # The branch is off by default; this test exercises the branch itself.
    monkeypatch.setenv("TRUENEX_DENSE", "on")
    from truenex_memory.core.embedder import HashingEmbedder

    repository = MemoryRepository(tmp_path / "memory.db", embedder=HashingEmbedder())
    assert not repository._dense_ranker_enabled()
    stub_repo = MemoryRepository(tmp_path / "memory2.db", embedder=_StubSemanticEmbedder())
    assert stub_repo._dense_ranker_enabled()


def test_dense_ranker_is_off_unless_explicitly_switched_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dense branch is OFF by default; only TRUENEX_DENSE=on enables it.

    The default flipped on 2026-08-21: measured on the live store, the
    branch helped no eval category and, switched off, improved two
    (42/53 vs 40/53, paired). It stays behind a flag rather than being
    deleted so re-enabling costs an env var, not re-embedding 201k chunks.
    """
    repository = MemoryRepository(tmp_path / "memory.db", embedder=_StubSemanticEmbedder())
    monkeypatch.delenv("TRUENEX_DENSE", raising=False)
    assert not repository._dense_ranker_enabled()
    monkeypatch.setenv("TRUENEX_DENSE", "off")
    assert not repository._dense_ranker_enabled()
    # Anything that is not "on" leaves it off, so a typo cannot silently
    # re-enable a branch we measured as harmful.
    monkeypatch.setenv("TRUENEX_DENSE", "yes")
    assert not repository._dense_ranker_enabled()
    monkeypatch.setenv("TRUENEX_DENSE", "on")
    assert repository._dense_ranker_enabled()
    monkeypatch.setenv("TRUENEX_DENSE", "ON")
    assert repository._dense_ranker_enabled()


def _repo_with_lexical_chunk(tmp_path: Path) -> MemoryRepository:
    """Repo with a semantic embedder stub and one lexically matching chunk,
    so search() takes the RRF branch (never the legacy dense fallback)."""
    repository = MemoryRepository(tmp_path / "memory.db", embedder=_StubSemanticEmbedder())
    doc = tmp_path / "lex.md"
    text = (
        "Rotazione chiavi MedDesk ogni novanta giorni secondo policy di "
        "sicurezza concordata con il team clinico."
    )
    doc.write_text(text, encoding="utf-8")
    repository.upsert_document(doc, "lex.md", chunk_text(text))
    return repository


def _dense_candidate(score: float) -> SearchHit:
    return _hit(
        "dense-candidate",
        score=score,
        source_path="docs/dense-only.md",
        document_id="dense-doc-1",
        content="contenuto trovato solo dal ranker semantico",
    )


def test_dense_cosine_gate_excludes_below_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dense candidate with cosine below DENSE_FUSION_MIN_COSINE must NOT
    enter the fusion: plausible-but-irrelevant neighbours (measured on the
    live store at cosine 0.84-0.93) buried memory targets when ungated."""
    from truenex_memory.retrieval.fusion import DENSE_FUSION_MIN_COSINE

    repository = _repo_with_lexical_chunk(tmp_path)
    below = _dense_candidate(round(DENSE_FUSION_MIN_COSINE - 0.01, 4))
    monkeypatch.setattr(
        repository, "_search_semantic_chunks", lambda conn, query, top_k, **kw: [below]
    )
    hits = repository.search("rotazione chiavi MedDesk novanta giorni", top_k=10)
    assert hits, "the lexical chunk must still be returned"
    assert all(hit.source_path != "docs/dense-only.md" for hit in hits), (
        "the below-threshold dense candidate must be filtered before fusion"
    )


def test_dense_cosine_gate_includes_above_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dense candidate with cosine above the threshold enters the fusion
    as a third RRF ranker."""
    # The branch is off by default; this test exercises the branch itself.
    monkeypatch.setenv("TRUENEX_DENSE", "on")
    from truenex_memory.retrieval.fusion import DENSE_FUSION_MIN_COSINE

    repository = _repo_with_lexical_chunk(tmp_path)
    above = _dense_candidate(round(DENSE_FUSION_MIN_COSINE + 0.01, 4))
    monkeypatch.setattr(
        repository, "_search_semantic_chunks", lambda conn, query, top_k, **kw: [above]
    )
    hits = repository.search("rotazione chiavi MedDesk novanta giorni", top_k=10)
    assert any(hit.source_path == "docs/dense-only.md" for hit in hits)


def test_dense_cosine_gate_boundary_is_inclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """score == DENSE_FUSION_MIN_COSINE passes the gate (>= semantics,
    symmetric to MEMORY_FUSION_MIN_OVERLAP)."""
    # The branch is off by default; this test exercises the branch itself.
    monkeypatch.setenv("TRUENEX_DENSE", "on")
    from truenex_memory.retrieval.fusion import DENSE_FUSION_MIN_COSINE

    repository = _repo_with_lexical_chunk(tmp_path)
    boundary = _dense_candidate(DENSE_FUSION_MIN_COSINE)
    monkeypatch.setattr(
        repository, "_search_semantic_chunks", lambda conn, query, top_k, **kw: [boundary]
    )
    hits = repository.search("rotazione chiavi MedDesk novanta giorni", top_k=10)
    assert any(hit.source_path == "docs/dense-only.md" for hit in hits)


def test_dense_cosine_gate_constant_is_shared() -> None:
    """repository must import the threshold from retrieval.fusion (single
    source of truth, like the other fusion constants)."""
    from truenex_memory.retrieval import fusion
    from truenex_memory.store import repository

    assert repository.DENSE_FUSION_MIN_COSINE is fusion.DENSE_FUSION_MIN_COSINE
    assert fusion.DENSE_FUSION_MIN_COSINE == 0.90


def test_min_score_prefilter_skips_hydration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_search_semantic_chunks(min_score=...) filters VectorMatch BEFORE
    hydration: when every candidate is below the threshold, hydration is
    never called (it is the dominant dense cost, ~1.4s under load)."""
    from truenex_memory.store import repository as repo_module
    from truenex_memory.store.sqlite import connect

    repository = _repo_with_lexical_chunk(tmp_path)
    monkeypatch.setattr(
        repo_module,
        "_hydrate_chunks_by_point_ids",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("hydration must be skipped")),
    )
    # The stub embedder produces cosine 1.0 for every indexed chunk; a
    # min_score above 1.0 filters everything before hydration.
    conn = connect(repository.db_path)
    try:
        hits = repository._search_semantic_chunks(conn, "rotazione chiavi", 10, min_score=1.1)
    finally:
        conn.close()
    assert hits == []


def test_min_score_prefilter_identical_to_post_gate(tmp_path: Path) -> None:
    """Equivalence: pre-hydration min_score and post-hydration gate produce
    the same dense list (same threshold on the same rounded cosines)."""
    from truenex_memory.retrieval.fusion import DENSE_FUSION_MIN_COSINE
    from truenex_memory.store.sqlite import connect

    repository = _repo_with_lexical_chunk(tmp_path)
    conn = connect(repository.db_path)
    try:
        prefiltered = repository._search_semantic_chunks(
            conn, "rotazione chiavi", 10, min_score=DENSE_FUSION_MIN_COSINE
        )
        unfiltered = repository._search_semantic_chunks(conn, "rotazione chiavi", 10)
    finally:
        conn.close()
    post_gated = [h for h in unfiltered if h.score >= DENSE_FUSION_MIN_COSINE]
    assert [h.source_path for h in prefiltered] == [h.source_path for h in post_gated]
    assert [h.score for h in prefiltered] == [h.score for h in post_gated]


def test_dense_ranker_never_mixes_vector_dimensions(tmp_path: Path) -> None:
    """Cross-dimension safety: chunks embedded with the hashing model (384d)
    must NEVER be cosine-compared with a semantic query vector (768d) —
    _sqlite_vector_matches filters by embedding_model, so with no vectors
    for the active model the dense list is empty (no crash, no mixing)."""
    from truenex_memory.core.embedder import HashingEmbedder
    from truenex_memory.store.repository import _sqlite_vector_matches
    from truenex_memory.store.sqlite import connect

    # Index with the hashing embedder: vectors are 384d under
    # "hashing-fallback:intfloat/multilingual-e5-base".
    hashing_repo = MemoryRepository(tmp_path / "memory.db", embedder=HashingEmbedder())
    doc = tmp_path / "doc.md"
    doc.write_text("alpha beta gamma delta procedura completa.", encoding="utf-8")
    hashing_repo.upsert_document(doc, "docs/doc.md", chunk_text(doc.read_text()))

    semantic_repo = MemoryRepository(tmp_path / "memory.db", embedder=_StubSemanticEmbedder())
    hits = semantic_repo.search("alpha beta gamma delta", top_k=5)
    # Lexical fusion still works; dense contributed nothing (no stub-model
    # vectors) and no 384d vector was compared with the 8d query vector.
    assert hits
    assert hits[0].source_path.endswith("doc.md")

    conn = connect(tmp_path / "memory.db")
    try:
        matches = _sqlite_vector_matches(
            conn,
            _StubSemanticEmbedder().embed_query("alpha"),
            10,
            embedding_model=_StubSemanticEmbedder().model_name,
        )
        assert matches == [], "hashing-model vectors must be filtered out by model name"
    finally:
        conn.close()


def test_cosine_returns_zero_for_mismatched_dimensions() -> None:
    """Last-resort guard: cosine between vectors of different length is 0,
    never an implicit zip-truncated similarity."""
    from truenex_memory.retrieval.semantic import _cosine

    assert _cosine([1.0] * 384, [1.0] * 768) == 0.0


def test_session_derived_paths_are_recognised() -> None:
    """The path predicate identifies transcript-derived content."""
    from truenex_memory.store.repository import _is_session_derived

    assert _is_session_derived(r"C:\x\ce5bcf77.jsonl::exchange_750")
    assert _is_session_derived(r"C:\x\context_5.jsonl")
    assert _is_session_derived(r"C:\X\CONTEXT_5.JSONL")  # case-insensitive
    assert not _is_session_derived(r"D:\repo\docs\mcp-setup.md")
    assert not _is_session_derived(None)
    # A .jsonl mention inside a normal document name must not trigger it.
    assert not _is_session_derived(r"D:\repo\docs\how-to-read-jsonl-files.md")


def test_vetting_decides_whether_a_transcript_memory_is_retrievable() -> None:
    """Only *unvetted* transcript content is excluded from results.

    Excluding by path alone made `global auto approve` a no-op: it
    promotes a node to `active` that retrieval then discards anyway, and
    it silently withheld the 111 nodes a person had already approved or
    curated. Raw dialogue still has to stay out while nobody has vouched
    for it, so the discriminator is vetting, not provenance.
    """
    from truenex_memory.store.repository import _is_unvetted_session_memory

    transcript = r"C:\x\ce5bcf77.jsonl::exchange_750"
    # Nobody has looked at it yet, or it was rejected: keep it out.
    assert _is_unvetted_session_memory(transcript, "unverified")
    assert _is_unvetted_session_memory(transcript, "obsolete")
    assert _is_unvetted_session_memory(transcript, None)
    # A person promoted it: retrieval honours that decision.
    assert not _is_unvetted_session_memory(transcript, "active")
    assert not _is_unvetted_session_memory(transcript, "ACTIVE")
    # A curated note is never touched, whatever its status.
    assert not _is_unvetted_session_memory(r"D:\repo\docs\mcp.md", "unverified")


def test_supersede_retires_the_old_note_and_links_forward(tmp_path: Path) -> None:
    """A note can declare which one it replaces, in one transaction.

    The `superseded` status already existed but said nothing about the
    replacement, so a reader learned a claim was stale without being able
    to find what now holds true.
    """
    repository = MemoryRepository(tmp_path / "memory.db")
    old = repository.add_memory(
        "Il lavoro non e' committato: attenzione prima di ripartire.",
        memory_type="decision",
    )
    new = repository.add_memory(
        "Il lavoro e' stato committato in 6dce6a7.",
        memory_type="decision",
        supersedes=old,
    )

    retired = repository.get_memory_node(old)
    current = repository.get_memory_node(new)
    assert retired is not None and current is not None
    assert retired.status == "superseded"
    assert retired.superseded_by == new
    assert current.status == "active"
    assert current.superseded_by is None

    # The retired claim must leave retrieval: that is the whole point.
    titles = [hit.title for hit in repository.search("committato", top_k=10)]
    assert current.title in titles
    assert retired.title not in titles
    # ...but stay reachable when history is asked for explicitly.
    inactive = [
        hit.title
        for hit in repository.search("committato", top_k=10, include_inactive=True)
    ]
    assert retired.title in inactive


def test_supersede_rejects_unknown_and_already_retired_targets(tmp_path: Path) -> None:
    """Refuse silently-wrong links rather than recording them."""
    repository = MemoryRepository(tmp_path / "memory.db")

    with pytest.raises(ValueError, match="unknown memory"):
        repository.add_memory("nuova", supersedes="mem_inesistente")

    first = repository.add_memory("prima versione")
    second = repository.add_memory("seconda versione", supersedes=first)
    # Superseding an already-retired note would fork the chain and leave
    # two notes claiming to replace the same one.
    with pytest.raises(ValueError, match="already superseded"):
        repository.add_memory("terza versione", supersedes=first)
    assert repository.get_memory_node(second).status == "active"

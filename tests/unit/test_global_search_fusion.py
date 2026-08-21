"""Regression tests for RRF fusion in `build_global_search` (CLI path).

The MCP path (`MemoryRepository.search`) was fixed in commit adfda27; the
CLI `global search` path merged raw memory scores (0-10) with raw chunk
BM25 scores (hundreds) in one ranking, so memories were invisible.  These
tests pin the RRF-based behavior on the global search path.
"""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.ingestion.global_search import (
    GlobalSearchHit,
    build_global_search,
)
from truenex_memory.retrieval.fusion import (
    CHUNK_SOURCE_WEIGHT,
    MEMORY_FUSION_MIN_OVERLAP,
    MEMORY_SOURCE_WEIGHT,
    RRF_K,
    reciprocal_rank_fusion,
)
from truenex_memory.store.repository import MemoryRepository


RRF_SCORE_MAX = round((MEMORY_SOURCE_WEIGHT + CHUNK_SOURCE_WEIGHT) / (RRF_K + 1), 6)

PHRASE = "un backup che non hai mai provato a ripristinare non e un backup e un auspicio"


def _hit(
    hit_id: str,
    *,
    kind: str = "document_chunk",
    title: str = "title",
    score: float = 1.0,
    source_path: str | None = None,
    content: str = "content",
) -> GlobalSearchHit:
    return GlobalSearchHit(
        id=hit_id,
        kind=kind,
        title=title,
        content=content,
        content_excerpt=content,
        source_path=source_path,
        heading_path=None,
        memory_type="document_chunk" if kind == "document_chunk" else "note",
        status="active",
        score=score,
    )


def _index_doc(repository: MemoryRepository, tmp_path: Path, relative_path: str, text: str) -> None:
    doc_path = tmp_path / Path(relative_path).name
    doc_path.write_text(text, encoding="utf-8")
    repository.upsert_document(doc_path, relative_path, chunk_text(text))


def _repository_with_corpus(tmp_path: Path, memory_content: str) -> MemoryRepository:
    """One memory plus a corpus where chunk BM25 scores reach the hundreds.

    FTS5 idf is only large for tokens with low document frequency, so the
    corpus is 200 filler documents (no query tokens, they just inflate N)
    plus 12 documents sharing the rarer query tokens — same shape as the
    478k-chunk production index where the bug was observed.
    """
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.add_memory(memory_content, memory_type="decision")
    for index in range(200):
        _index_doc(
            repository,
            tmp_path,
            f"docs/filler_{index:03d}.md",
            f"Filler {index}: zqxwv jklmp qwerty zxcvbn plokm ijnuh bygvt fcdxs.",
        )
    for index in range(12):
        _index_doc(
            repository,
            tmp_path,
            f"docs/match_{index:03d}.md",
            f"Nota {index}: il backup va provato, non basta mai un auspicio; "
            "serve ripristinare davvero.",
        )
    return repository


def test_distinctive_memory_ranks_first_with_many_competing_chunks(tmp_path: Path) -> None:
    """Acceptance criterion (red on pre-patch code): the memory survives the
    top_k cut and ranks first even with chunk scores in the hundreds."""
    _repository_with_corpus(tmp_path, f"Decisione operativa: {PHRASE}.")

    report = build_global_search(tmp_path / "memory.db", PHRASE, top_k=5)

    assert report.results, "search must return results"
    assert report.results[0].kind == "memory_node"
    assert "auspicio" in report.results[0].content
    memory_hits = [hit for hit in report.results if hit.kind == "memory_node"]
    assert memory_hits, "the memory must survive the top_k cut"


def test_mixed_query_returns_both_kinds_on_single_scale(tmp_path: Path) -> None:
    """A query matching both a memory and chunks returns both; all exposed
    scores are on the single documented RRF scale (never hundreds)."""
    _repository_with_corpus(tmp_path, f"Decisione operativa: {PHRASE}.")

    report = build_global_search(tmp_path / "memory.db", PHRASE, top_k=10)

    kinds = {hit.kind for hit in report.results}
    assert kinds == {"memory_node", "document_chunk"}
    assert all(0 < hit.score <= RRF_SCORE_MAX for hit in report.results)
    assert report.results[0].kind == "memory_node"


def test_reciprocal_rank_fusion_unit() -> None:
    """Unit test of the generic fusion: expected order, duplicates inside one
    list counted once at their best rank, distinct same-file chunks kept
    separate."""
    memories = [
        _hit("mem_1", kind="memory_node", title="mem best", score=10.0, content="m1"),
        _hit("mem_2", kind="memory_node", title="mem second", score=5.0, content="m2"),
    ]
    chunks = [
        _hit("c1", title="same.md", score=380.0, source_path="docs/same.md", content="alpha"),
        _hit("c2", title="same.md", score=370.0, source_path="docs/same.md", content="beta"),
        _hit("c3", title="same.md", score=90.0, source_path="docs/same.md", content=" ALPHA "),
    ]

    fused = reciprocal_rank_fusion(
        [(MEMORY_SOURCE_WEIGHT, memories), (CHUNK_SOURCE_WEIGHT, chunks)],
        key_fn=lambda hit: (
            hit.kind,
            hit.source_path or hit.id,
            hit.title,
            " ".join(hit.content.split()).casefold(),
        ),
        score_fn=lambda hit: hit.score,
    )

    by_content = {hit.content: score for score, hit in fused}
    # Distinct same-file chunks stay distinct. The duplicate (alpha, at
    # ranks 1 and 3 of the SAME list) merges into one hit and is scored at
    # its BEST rank only — it is one piece of evidence, not two. Summing it
    # let a chat export with 60 byte-identical separator chunks accumulate
    # ~6x any real answer's score and take rank 1 on four documentation
    # queries; counting once recovered +3 eval cases with no regression.
    assert len(fused) == 4
    assert by_content["alpha"] == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 1), 6)
    assert by_content["beta"] == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 2), 6)
    # Order is by rank within each source, with no duplicate premium. The
    # rank-1 memory and the rank-1 chunk now tie exactly (equal weights), so
    # assert the tier they occupy rather than a tie-break that carries no
    # meaning.
    assert {hit.id for _, hit in fused[:2]} == {"mem_1", "c1"}
    assert {hit.id for _, hit in fused[2:]} == {"mem_2", "c2"}
    assert fused[0][0] == fused[1][0] == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 1), 6)
    assert all(0 < score <= RRF_SCORE_MAX for score, _ in fused)

    # Input order is irrelevant: each list is re-ranked by its own score.
    shuffled = reciprocal_rank_fusion(
        [(MEMORY_SOURCE_WEIGHT, list(reversed(memories))), (CHUNK_SOURCE_WEIGHT, list(reversed(chunks)))],
        key_fn=lambda hit: (
            hit.kind,
            hit.source_path or hit.id,
            hit.title,
            " ".join(hit.content.split()).casefold(),
        ),
        score_fn=lambda hit: hit.score,
    )
    # The property under test is that input order does not matter, so compare
    # against the unshuffled result instead of restating a literal order.
    assert [hit.id for _, hit in shuffled] == [hit.id for _, hit in fused]
    assert [score for score, _ in shuffled] == [score for score, _ in fused]


def test_kind_filter_single_source_degenerates_to_source_order(tmp_path: Path) -> None:
    """kind_filter='memory' / 'chunks' activates one source only: the fusion
    degenerates to that source's own raw-score ranking."""
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.add_memory(
        f"Decisione completa: {PHRASE}.", memory_type="decision", title="full overlap"
    )
    repository.add_memory(
        "Nota parziale: un backup non provato.", memory_type="note", title="partial overlap"
    )
    for index in range(12):
        _index_doc(
            repository,
            tmp_path,
            f"docs/match_{index:03d}.md",
            f"Nota {index}: il backup va provato, non basta mai un auspicio; "
            "serve ripristinare davvero.",
        )
    db_path = tmp_path / "memory.db"

    memory_report = build_global_search(db_path, PHRASE, top_k=10, kind_filter="memory")
    assert memory_report.results
    assert all(hit.kind == "memory_node" for hit in memory_report.results)
    # Degenerate fusion keeps the source's own ranking: full overlap first.
    assert memory_report.results[0].title == "full overlap"
    assert (
        memory_report.results[0].score == round(MEMORY_SOURCE_WEIGHT / (RRF_K + 1), 6)
    )

    chunk_report = build_global_search(db_path, PHRASE, top_k=10, kind_filter="chunks")
    assert chunk_report.results
    assert all(hit.kind == "document_chunk" for hit in chunk_report.results)
    assert all(0 < hit.score <= round(CHUNK_SOURCE_WEIGHT / (RRF_K + 1), 6) for hit in chunk_report.results)


def test_rrf_constants_parity_with_repository() -> None:
    """The RRF constants duplicated in store.repository (MCP path) and
    retrieval.fusion (CLI path) must stay aligned. The pre-fusion memory
    relevance gate is NOT duplicated: both paths import it from
    retrieval.fusion (single source)."""
    from truenex_memory.store import repository

    assert repository.RRF_K == RRF_K
    assert repository.MEMORY_SOURCE_WEIGHT == MEMORY_SOURCE_WEIGHT
    assert repository.CHUNK_SOURCE_WEIGHT == CHUNK_SOURCE_WEIGHT
    assert repository.MEMORY_FUSION_MIN_OVERLAP is MEMORY_FUSION_MIN_OVERLAP


def test_memory_gate_excludes_weak_memories_and_chunks_rise(tmp_path: Path) -> None:
    """Same pre-fusion gate as the MCP path: a memory whose overlap ratio
    (here scaled 0-10) is below MEMORY_FUSION_MIN_OVERLAP must not enter
    the fusion, so document chunks surface instead."""
    repository = MemoryRepository(tmp_path / "memory.db")
    # Query has 4 tokens; the memory covers only "alpha" -> overlap 2.5/10.
    repository.add_memory("Nota rapida su alpha e basta.", memory_type="note", title="weak alpha")
    _index_doc(
        repository,
        tmp_path,
        "docs/quattro.md",
        "alpha beta gamma delta: procedura completa del tutto.",
    )

    report = build_global_search(tmp_path / "memory.db", "alpha beta gamma delta", top_k=5)

    assert report.results, "search must return results"
    assert all(hit.kind == "document_chunk" for hit in report.results), (
        "weak memory (overlap 0.25 < gate) must be excluded from fusion"
    )


def test_memory_gate_keeps_strong_memories_first(tmp_path: Path) -> None:
    """A memory at or above the gate keeps first-class status and outranks
    chunks at equal position in its own ranking."""
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.add_memory(
        "Decisione: alpha beta gamma delta approvate tutte.",
        memory_type="decision",
        title="strong decision",
    )
    _index_doc(
        repository,
        tmp_path,
        "docs/quattro.md",
        "alpha beta gamma delta: procedura completa del tutto.",
    )

    report = build_global_search(tmp_path / "memory.db", "alpha beta gamma delta", top_k=5)

    assert report.results, "search must return results"
    assert report.results[0].kind == "memory_node"
    assert any(hit.kind == "document_chunk" for hit in report.results)


def test_memory_gate_boundary_is_inclusive(tmp_path: Path) -> None:
    """Boundary on the 0-10 score scale: a memory with overlap ratio exactly
    MEMORY_FUSION_MIN_OVERLAP (score == gate * 10.0) passes the gate."""
    repository = MemoryRepository(tmp_path / "memory.db")
    # Query has 4 tokens; the memory covers exactly 2 -> score 5.0/10.
    repository.add_memory(
        "Nota su alpha beta e nient'altro.", memory_type="note", title="boundary"
    )
    _index_doc(
        repository,
        tmp_path,
        "docs/quattro.md",
        "alpha beta gamma delta: procedura completa del tutto.",
    )

    assert 2 / 4 == MEMORY_FUSION_MIN_OVERLAP, (
        "test premise: the crafted memory must sit exactly on the gate boundary"
    )
    report = build_global_search(tmp_path / "memory.db", "alpha beta gamma delta", top_k=5)

    assert report.results, "search must return results"
    assert report.results[0].kind == "memory_node", (
        "a memory exactly at the gate boundary must be included (>= is inclusive)"
    )


def test_weak_memory_as_only_lexical_evidence_is_kept(tmp_path: Path) -> None:
    """Conditional gate: with no chunk hits the gate is skipped — a weak
    memory that is the only lexical evidence must be kept, not discarded."""
    repository = MemoryRepository(tmp_path / "memory.db")
    # Weak memory: covers 1 of 4 query tokens -> score 2.5/10 < gate.
    repository.add_memory("Nota rapida su alpha e basta.", memory_type="note", title="weak alpha")
    # Document with NO query token: no chunk hits at all.
    _index_doc(repository, tmp_path, "docs/altro.md", "zzz yyy xxx www vvv.")

    report = build_global_search(tmp_path / "memory.db", "alpha beta gamma delta", top_k=5)

    assert report.results, "search must return results"
    assert report.results[0].kind == "memory_node", (
        "with no chunk evidence the weak memory must be kept (gate skipped)"
    )


def test_json_report_marks_rrf_score_scale(tmp_path: Path) -> None:
    """The JSON report must declare the score scale so consumers can
    distinguish RRF scores from raw pre-RRF mixed-scale values."""
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.add_memory("Nota distintiva zqalpha token.", memory_type="note")

    report = build_global_search(tmp_path / "memory.db", "zqalpha", top_k=5)

    assert report.to_dict()["score_scale"] == "rrf-k60"

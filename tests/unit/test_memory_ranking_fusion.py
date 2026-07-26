"""Regression tests for Reciprocal Rank Fusion of memory and chunk hits.

Before the RRF fusion, ``MemoryRepository.search()`` merged raw memory
scores (0.0-1.0) with raw chunk BM25 scores (hundreds) in a single ranking,
so every memory always ranked below every chunk and was cut by ``top_k``.
These tests pin the fixed behavior: memories are first-class evidence.
"""

from __future__ import annotations

from pathlib import Path

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

    # A rank-1 memory outranks a rank-1 chunk despite the raw-score gap.
    # Order by RRF math: 1.5/61 > 1.5/62 > 1.0/61 > 1.0/62.
    assert fused[0].title == "mem best"
    assert fused[0].score == round(MEMORY_SOURCE_WEIGHT / (RRF_K + 1), 6)
    assert fused[1].title == "mem second"
    assert fused[1].score == round(MEMORY_SOURCE_WEIGHT / (RRF_K + 2), 6)
    assert fused[2].title == "chunk best"
    assert fused[2].score == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 1), 6)
    assert fused[3].title == "chunk second"
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
    identity key includes normalized content. Only true duplicates (same
    normalized content) merge and sum their RRF contributions."""
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
    # True duplicate sums both RRF contributions: chunk_a is rank 1 (score
    # 380) and its mirror is rank 4 (score 90) in the score-sorted list.
    expected_alpha = round(CHUNK_SOURCE_WEIGHT / (RRF_K + 1) + CHUNK_SOURCE_WEIGHT / (RRF_K + 4), 6)
    assert by_content["alpha body"].score == expected_alpha
    # Non-duplicate hits keep their single-rank contribution.
    assert by_content["beta body"].score == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 2), 6)
    assert by_content["gamma"].score == round(CHUNK_SOURCE_WEIGHT / (RRF_K + 3), 6)
    # No score can exceed the documented single-source-per-content maximum.
    max_possible = (MEMORY_SOURCE_WEIGHT + CHUNK_SOURCE_WEIGHT) / (RRF_K + 1)
    assert all(hit.score <= round(max_possible, 6) for hit in fused)
    # Ordering: summed alpha first, then beta, then gamma.
    assert [hit.content for hit in fused] == ["alpha body", "beta body", "gamma"]


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

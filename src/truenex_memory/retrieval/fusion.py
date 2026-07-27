"""Shared Reciprocal Rank Fusion (RRF) for heterogeneous rankers.

The global store has two rankers whose raw scores live on incomparable
scales:

- memory nodes: token-overlap ratio (0.0-1.0 in `MemoryRepository.search`,
  0-10 in `build_global_search`);
- document chunks: rescaled FTS5/BM25 scores (hundreds on large indexes).

Merging them by raw score always buries memories below chunks.  RRF merges
by *position* instead, which requires no retuning of either ranker.

The constants below MUST stay aligned with the ones in
`truenex_memory.store.repository` (`_fuse_ranked_hits`), which implements
the same fusion inline for the MCP-facing search path; both paths must
expose the same score scale and the same memory-over-chunk preference.
"""

from __future__ import annotations

from typing import Callable, Hashable, Iterable, Sequence, TypeVar

T = TypeVar("T")

# Standard RRF smoothing constant (Cormack et al., 2009): damps the
# influence of top ranks so rank 1 does not dominate rank 5.
RRF_K = 60
# Memories are curated knowledge written explicitly by an agent or a
# person, not text extracted automatically from a file.  At equal position
# within their own ranking they must outrank document chunks.
MEMORY_SOURCE_WEIGHT = 1.5
CHUNK_SOURCE_WEIGHT = 1.0
# Dense (semantic) chunk candidates support the lexical ones but must not
# overwhelm them: at equal position within their own ranking they score
# slightly below a lexical chunk. Used only by the MCP path
# (`MemoryRepository.search`), where the dense ranker is active when the
# configured embedder is not the hashing fallback.
DENSE_SOURCE_WEIGHT = 0.9

# Relevance gate on memory hits BEFORE fusion, expressed as the minimum
# token-overlap ratio (fraction of query tokens covered by the memory,
# 0.0-1.0).  RRF merges by rank and ignores raw scores, so without this
# gate ANY memory matching a single query token enters the fused ranking
# with the 1.5 source weight: a memory at rank 32 (1.5/(60+32) ~= 0.0163)
# still beats the best document chunk (1.0/61 ~= 0.0164).  On a store with
# thousands of active memories, broad topical queries match 30+ weak
# memories and every document chunk is pushed out of top_k (measured on
# the live store, eval baseline 2026-07-27: top-40 all memories, target
# chunks at fused rank 35-38).  The memory source is the only one whose
# raw score has a direct interpretation (fraction of query tokens
# covered), so it is the right place for a relevance gate: memories below
# the threshold are not "first-class curated knowledge", they are noise.
# Chosen empirically on the live store (see docs/eval/): 0.5 frees
# document targets for broad topical queries while every curated memory
# recall case (overlap 1.0) keeps rank 1.
MEMORY_FUSION_MIN_OVERLAP = 0.5


def reciprocal_rank_fusion(
    weighted_lists: Iterable[tuple[float, Sequence[T]]],
    *,
    key_fn: Callable[[T], Hashable],
    score_fn: Callable[[T], float],
) -> list[tuple[float, T]]:
    """Merge ranked lists by position with Reciprocal Rank Fusion.

    ``weighted_lists`` is an iterable of ``(weight, items)`` pairs, one per
    ranker.  Each list is (re)sorted by ``score_fn`` descending — input
    order is irrelevant — and ranks are 1-based within its own list::

        fused(item) = sum over lists of weight / (RRF_K + rank_in_list)

    Items are identified by ``key_fn``; true duplicates (same key appearing
    in one or more lists) have their RRF contributions summed and keep the
    first-seen item as representative.

    Returns ``(fused_score, item)`` pairs sorted by fused score descending
    (ties keep first-seen order, deterministic given equal inputs).  The
    fused score is rounded to 6 decimals and lives on a single small
    positive scale.  ``sum(weights) / (RRF_K + 1)`` — with the module
    constants, ``(MEMORY_SOURCE_WEIGHT + CHUNK_SOURCE_WEIGHT) / 61 ≈
    0.040984`` — is the maximum for an item appearing *once per list*; it
    is NOT a hard bound, because true duplicates (same identity key, e.g.
    mirrored copies of a chunk) sum their contributions and can exceed it.

    These scores are NOT comparable with raw pre-RRF scores, which mixed
    memory ratios and BM25 values in one field.  With a single non-empty
    list the fusion degenerates to that list's own ranking (each item gets
    ``weight / (RRF_K + rank)``), so single-source searches keep their
    original order.
    """

    scores: dict[Hashable, float] = {}
    representatives: dict[Hashable, T] = {}
    for weight, items in weighted_lists:
        ranked = sorted(items, key=score_fn, reverse=True)
        for rank, item in enumerate(ranked, start=1):
            key = key_fn(item)
            scores[key] = scores.get(key, 0.0) + weight / (RRF_K + rank)
            representatives.setdefault(key, item)

    fused = [(round(score, 6), representatives[key]) for key, score in scores.items()]
    fused.sort(key=lambda pair: pair[0], reverse=True)
    return fused

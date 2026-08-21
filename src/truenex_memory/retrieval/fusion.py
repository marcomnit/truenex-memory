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
# Memories and document chunks now enter fusion at equal weight.
#
# The old 1.5 privilege was not a mild preference: with RRF_K = 60 it made
# the first 31 memories that clear the overlap gate outrank the best chunk
# in the corpus unconditionally, since 1.5/(60+r) >= 1.0/61 for r <= 31.
# Measured on the live store (2026-08-20, 14 realistic documentation
# questions): at 1.5, eleven of fourteen questions returned NO document at
# all and only 5 document chunks appeared across all top-5s; at 1.0, zero
# questions were document-free and 33 chunks appeared, while the committed
# eval set held steady (memory-recall 12/14, real-logs 1/5, bug-report 5/6).
# 0.8 was tested too and overshoots badly — memory-recall collapses to 0/14.
#
# Keep aligned with the copy in store.repository; a parity test enforces it.
MEMORY_SOURCE_WEIGHT = 1.0
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

# A memory at or above this overlap ratio bypasses the rarest-term rule in
# `_require_most_informative_token`. Every memory-recall eval case is a
# near-verbatim quotation of the memory it targets (overlap 1.0 over 7-16
# content terms), so this is what guarantees the IDF rule cannot regress
# curated recall while it prunes memories that share only generic words.
MEMORY_GATE_NEAR_VERBATIM_OVERLAP = 0.9

# How many chunks of the SAME document may appear in the results. Content
# deduplication does not catch this — three different chunks of one file are
# three distinct pieces of content — but they spend answer slots without
# adding information: on a real question about setting up a Git bridge, the
# same `docs/git-bridge-setup.md` held all three top positions, so two of
# five slots said nothing new. None disables the cap.
#
# Swept on all three eval sets: the effect is monotonic and 1 wins.
# On the held-out set, cap None -> 4/32, 3 -> 4/32, 2 -> 5/32, 1 -> 6/32
# (MRR 0.094 -> 0.108), while the committed 53-case set holds at 40/53
# throughout with a marginally better MRR. Every freed slot goes to a
# different document, which is the only way a fifth result adds anything.
MAX_CHUNKS_PER_DOCUMENT = 1

# Relevance gate on DENSE candidates BEFORE fusion, expressed as the
# minimum cosine similarity (0.0-1.0) against the query vector. Symmetric
# to MEMORY_FUSION_MIN_OVERLAP: RRF ignores raw scores, so without this
# gate EVERY dense neighbour enters the fused ranking with the 0.9 source
# weight, and on ~478k embedded chunks every query has "plausible but
# irrelevant" neighbours (measured on the live store, eval 2026-07-27:
# the chunks that buried memory-recall targets sit at cosine 0.84-0.93).
# The e5 vectors are L2-normalized, so the cosine IS interpretable.
# Chosen empirically (docs/eval/gate-sweep-2026-07-27.json): the "good"
# dense targets (real-logs r01/r02 at 0.867-0.877) and the "bad" ones
# overlap in the 0.85-0.90 band, so no threshold recovers the long-tail
# wins without re-introducing the memory-recall regressions.
#
# 2026-08-21: that entry claimed 0.90 "restores FULL parity with the
# dense-OFF baseline". It no longer holds — dense-OFF now measures BETTER
# than dense-ON at this gate (42/53 vs 40/53 on the 53-case eval, paired),
# so the gate is not a calibration that reaches parity, it is a switch that
# happens to mute a misaligned ranker. The branch is therefore off by
# default (see MemoryRepository._dense_ranker_enabled) and this threshold
# only applies when TRUENEX_DENSE=on. Do not read this constant as evidence
# that the dense branch earns its place.
DENSE_FUSION_MIN_COSINE = 0.90

# TRIED AND REJECTED, 2026-08-21 — routing the dense branch by lexical
# specificity. The motivation was real: on 30 paraphrase questions written by
# another agent (which deliberately avoid the target's vocabulary) the whole
# lexical+memory system answers 2, while the dense ranker ALONE has the target
# in its cosine top-5 on 3 and top-50 on 6, and the 0.90 gate admits exactly
# zero candidates there. The router fired when at most N of the lexical
# candidates carried the query's rarest term — a signal that separates the two
# question classes cleanly (median 34 lexical vs 3 paraphrase).
#
# All three variants lost more than they gained on the 53-case set:
#   N<=2, top-100 dense: paraphrases 2->3, existing 42->38
#   N==0, top-100 dense: paraphrases 2->3, existing 42->40
#   N==0, top-5   dense: paraphrases 2->2, existing 42->40
# Admitted by rank the dense candidates are too noisy to help even where the
# lexical branch has nothing. The conclusion is not "route better": it is that
# this embedder does not rank this corpus usefully, so the paraphrase gap needs
# a different model, not a different gate.


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

    Items are identified by ``key_fn``.  A key repeated WITHIN one list
    contributes once, at its best rank; the same key appearing in SEVERAL
    lists sums its contributions, which is the corroboration signal RRF
    exists for.  The first-seen item is kept as representative.

    Returns ``(fused_score, item)`` pairs sorted by fused score descending
    (ties keep first-seen order, deterministic given equal inputs).  The
    fused score is rounded to 6 decimals and lives on a single small
    positive scale.  ``sum(weights) / (RRF_K + 1)`` — with the module
    constants, ``(MEMORY_SOURCE_WEIGHT + CHUNK_SOURCE_WEIGHT) / 61 ≈
    0.040984`` — is the exact maximum: an item can contribute at most once
    per list, so no amount of repeated content inside a single list can
    exceed it.

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
        # Within ONE list an identity contributes once, at its best rank.
        # Summing every occurrence conflates two opposite things: the same
        # item found by two different rankers (real corroboration, and the
        # sum across lists still rewards it) and the same content repeated
        # inside one ranker's own results, which is a single piece of
        # evidence counted N times. Measured consequence of the latter: a
        # 2,559-chunk chat export containing 60 identical separator lines
        # accumulated ~6x the score of any real answer and took rank 1 on
        # four of twelve failing documentation queries.
        best_rank: dict[Hashable, int] = {}
        for rank, item in enumerate(ranked, start=1):
            key = key_fn(item)
            if key not in best_rank:
                best_rank[key] = rank
            representatives.setdefault(key, item)
        for key, rank in best_rank.items():
            scores[key] = scores.get(key, 0.0) + weight / (RRF_K + rank)

    fused = [(round(score, 6), representatives[key]) for key, score in scores.items()]
    fused.sort(key=lambda pair: pair[0], reverse=True)
    return fused

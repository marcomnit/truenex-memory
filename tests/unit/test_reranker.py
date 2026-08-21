"""Second-stage reranking: configuration, degradation, and the merge rules.

The cross-encoder itself is not exercised here — loading a 568M-parameter
model in a unit test would make the suite unusable. What IS pinned is every
rule around it, because each one was learned from a measured regression:

- memories are not reranked (a blanket rerank collapsed `bug-report` 6/6 ->
  2/6, four of those six cases target a memory node);
- the reranker's order is FUSED with the first stage, not substituted
  (substitution won 4 cases and lost 9 on the committed set);
- any failure returns the first stage untouched.
"""

from __future__ import annotations

import pytest

from truenex_memory.retrieval.reranker import (
    DEFAULT_RERANKER_MODEL,
    RERANK_CANDIDATE_LIMIT,
    CrossEncoderReranker,
    RerankerConfig,
    reranker_config_from_env,
)
from truenex_memory.store.models import SearchHit
from truenex_memory.store.repository import _rerank_hits


# ── configuration ─────────────────────────────────────────────────────────

def test_reranking_is_off_unless_explicitly_switched_on(monkeypatch) -> None:
    """A 568M model must never load because someone ran a search."""

    monkeypatch.delenv("TRUENEX_RERANKER", raising=False)
    assert reranker_config_from_env().enabled is False
    monkeypatch.setenv("TRUENEX_RERANKER", "off")
    assert reranker_config_from_env().enabled is False
    # Anything that is not "on" leaves it off, so a typo cannot silently
    # add seconds to every query.
    monkeypatch.setenv("TRUENEX_RERANKER", "yes")
    assert reranker_config_from_env().enabled is False
    monkeypatch.setenv("TRUENEX_RERANKER", "ON")
    assert reranker_config_from_env().enabled is True


def test_model_and_budget_are_overridable(monkeypatch) -> None:
    monkeypatch.setenv("TRUENEX_RERANKER", "on")
    monkeypatch.setenv("TRUENEX_RERANKER_MODEL", "some/other-model")
    monkeypatch.setenv("TRUENEX_RERANKER_CANDIDATES", "40")

    config = reranker_config_from_env()

    assert config.model_name == "some/other-model"
    assert config.candidate_limit == 40


def test_nonsense_budget_falls_back_to_the_default(monkeypatch) -> None:
    monkeypatch.setenv("TRUENEX_RERANKER_CANDIDATES", "molti")

    assert reranker_config_from_env().candidate_limit == RERANK_CANDIDATE_LIMIT


def test_empty_model_override_falls_back_to_the_default(monkeypatch) -> None:
    monkeypatch.setenv("TRUENEX_RERANKER_MODEL", "   ")

    assert reranker_config_from_env().model_name == DEFAULT_RERANKER_MODEL


def test_scoring_nothing_needs_no_model() -> None:
    assert CrossEncoderReranker("never/loaded").score("q", []) == []


# ── the merge rules ───────────────────────────────────────────────────────

def _chunk(name: str, score: float) -> SearchHit:
    return SearchHit(
        title=name, content=f"corpo di {name}", source_path=f"docs/{name}.md",
        heading_path=None, memory_type="document_chunk", status="active", score=score,
    )


def _memory(name: str, score: float) -> SearchHit:
    return SearchHit(
        title=name, content=f"nota {name}", source_path=None, heading_path=None,
        memory_type="decision", status="active", score=score,
    )


def _config(limit: int = 10) -> RerankerConfig:
    return RerankerConfig(enabled=True, model_name="stub", candidate_limit=limit)


def test_unusable_model_returns_the_first_stage_untouched(monkeypatch) -> None:
    """Reranking is an improvement, never a prerequisite."""

    monkeypatch.setattr(CrossEncoderReranker, "score", lambda self, q, texts: None)
    first = [_chunk("a", 0.03), _chunk("b", 0.02), _chunk("c", 0.01)]

    assert _rerank_hits("q", first, [], _config()) == first


def test_score_count_mismatch_returns_the_first_stage_untouched(monkeypatch) -> None:
    monkeypatch.setattr(CrossEncoderReranker, "score", lambda self, q, texts: [1.0])
    first = [_chunk("a", 0.03), _chunk("b", 0.02)]

    assert _rerank_hits("q", first, [], _config()) == first


def test_a_single_candidate_is_not_worth_a_forward_pass() -> None:
    first = [_chunk("a", 0.03)]

    assert _rerank_hits("q", first, [], _config()) == first


def test_memories_keep_the_position_the_fusion_gave_them(monkeypatch) -> None:
    """The rule that cost the most to learn.

    A blanket rerank collapsed `bug-report` from 6/6 to 2/6 because a
    cross-encoder judges passages, not curated notes. Memories therefore keep
    their fused slot and only chunks are reordered.
    """

    # Reverse whatever it is asked to score, so any reordering is visible.
    monkeypatch.setattr(
        CrossEncoderReranker,
        "score",
        lambda self, q, texts: [float(i) for i in range(len(texts))],
    )
    first = [_memory("m1", 0.04), _chunk("a", 0.03), _memory("m2", 0.02), _chunk("b", 0.01)]

    result = _rerank_hits("q", first, [], _config())

    assert [h.title for h in result if h.memory_type != "document_chunk"] == ["m1", "m2"]
    # positions 0 and 2 were the memories' slots and still are
    assert result[0].title == "m1"
    assert result[2].title == "m2"


def test_dense_only_candidates_can_enter_the_result(monkeypatch) -> None:
    """The whole point: a chunk the lexical branch never returned can win.

    On the paraphrase set the lexical list holds the target in 9/30 cases and
    the union with the dense list in 15/30; without admitting dense-only
    candidates the second stage has nothing new to promote.
    """

    def score(self, query, texts):
        # Favour whichever text mentions "atteso".
        return [10.0 if "atteso" in t else 0.0 for t in texts]

    monkeypatch.setattr(CrossEncoderReranker, "score", score)
    first = [_chunk("a", 0.03), _chunk("b", 0.02)]
    dense_only = SearchHit(
        title="atteso", content="questo e' il contenuto atteso", source_path="docs/atteso.md",
        heading_path=None, memory_type="document_chunk", status="active", score=0.88,
    )

    result = _rerank_hits("q", first, [dense_only], _config())

    assert "atteso" in [h.title for h in result]


def test_the_budget_is_respected(monkeypatch) -> None:
    seen: list[int] = []

    def score(self, query, texts):
        seen.append(len(texts))
        return [0.0] * len(texts)

    monkeypatch.setattr(CrossEncoderReranker, "score", score)
    first = [_chunk(f"c{i}", 1.0 / (i + 1)) for i in range(20)]
    dense = [
        SearchHit(
            title=f"d{i}", content=f"denso {i}", source_path=f"docs/d{i}.md",
            heading_path=None, memory_type="document_chunk", status="active", score=0.9,
        )
        for i in range(20)
    ]

    _rerank_hits("q", first, dense, _config(limit=6))

    assert seen == [6]


def test_first_stage_agreement_is_preserved(monkeypatch) -> None:
    """The fused order must not be the reranker's alone.

    Substituting the first stage won 4 cases and lost 9 on the committed set,
    so a candidate the first stage ranked first and the reranker ranked last
    must not fall to the bottom: both votes count.
    """

    # Reranker prefers the LAST first-stage candidate and hates the first.
    monkeypatch.setattr(
        CrossEncoderReranker,
        "score",
        lambda self, q, texts: list(range(len(texts))),
    )
    first = [_chunk(f"c{i}", 1.0 / (i + 1)) for i in range(6)]

    result = _rerank_hits("q", first, [], _config())
    by_title = {h.title: h.score for h in result}

    # RRF is symmetric, so "first for the first stage, last for the reranker"
    # ties exactly with its mirror image: both score 1/61 + 1/66. That tie IS
    # the guarantee — neither ranker can bury the other's favourite.
    assert by_title["c0"] == by_title["c5"]
    # And a candidate both rank in the middle scores below both extremes.
    assert by_title["c2"] < by_title["c0"]
    # The reranker's favourite is not dragged to the bottom by the first
    # stage's dislike, nor vice versa.
    titles = [h.title for h in result]
    assert set(titles[:2]) == {"c0", "c5"}

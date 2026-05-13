"""Tests for BM25 scoring module."""
from truenex_memory.retrieval.scoring import BM25, tokenize, source_boost, SOURCE_TYPE_BOOST


def test_bm25_ranks_relevant_doc_higher() -> None:
    corpus = [
        "sqlite database local storage",
        "python web server flask api",
        "sqlite query optimization index",
    ]
    tokenized = [tokenize(c) for c in corpus]
    bm25 = BM25(tokenized)
    scores = bm25.get_scores(tokenize("sqlite"))
    # doc 0 e doc 2 contengono "sqlite", doc 1 no
    assert scores[0] > 0
    assert scores[2] > 0
    assert scores[1] == 0.0


def test_bm25_empty_corpus_returns_empty() -> None:
    bm25 = BM25([])
    assert bm25.get_scores(["query"]) == []


def test_source_boost_values() -> None:
    assert source_boost("project_docs") == 1.0
    assert source_boost("agent_session") == 0.75
    assert source_boost(None) == 0.85
    assert source_boost("unknown_type") == 0.85


def test_bm25_term_frequency_matters() -> None:
    corpus = [
        "sqlite sqlite sqlite sqlite query",  # tf alto
        "sqlite query database",              # tf basso
    ]
    tokenized = [tokenize(c) for c in corpus]
    bm25 = BM25(tokenized)
    scores = bm25.get_scores(tokenize("sqlite"))
    assert scores[0] > scores[1]

"""BM25 keyword scoring for truenex-memory retrieval."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


SOURCE_TYPE_BOOST: dict[str, float] = {
    "project_docs": 1.0,
    "agent_session": 0.75,
}
DEFAULT_SOURCE_BOOST = 0.85


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words (Unicode-aware)."""
    return re.findall(r"\w+", text.lower())


def tokenize_set(text: str) -> set[str]:
    """Return unique lowercase tokens from text."""
    return set(tokenize(text))


@dataclass
class BM25:
    """Okapi BM25 scorer over a fixed corpus.

    Build once per query call with the candidate corpus, then call
    get_scores() to rank all documents against a query.
    """

    corpus: list[list[str]]
    k1: float = 1.5
    b: float = 0.75

    def __post_init__(self) -> None:
        self._N = len(self.corpus)
        self._avgdl = (
            sum(len(d) for d in self.corpus) / max(self._N, 1)
        )
        self._df: dict[str, int] = {}
        for doc in self.corpus:
            for term in set(doc):
                self._df[term] = self._df.get(term, 0) + 1

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log((self._N - df + 0.5) / (df + 0.5) + 1)

    def score(self, query_tokens: list[str], doc_tokens: list[str]) -> float:
        """BM25 score for a single document against the query."""
        dl = len(doc_tokens)
        tf_map: dict[str, int] = {}
        for t in doc_tokens:
            tf_map[t] = tf_map.get(t, 0) + 1
        result = 0.0
        for term in query_tokens:
            tf = tf_map.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf(term)
            num = tf * (self.k1 + 1)
            den = tf + self.k1 * (1.0 - self.b + self.b * dl / self._avgdl)
            result += idf * num / den
        return result

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        """Return BM25 score for every document in the corpus."""
        return [self.score(query_tokens, doc) for doc in self.corpus]


def source_boost(source_type: str | None) -> float:
    """Return the score multiplier for a given source_type."""
    if source_type is None:
        return DEFAULT_SOURCE_BOOST
    return SOURCE_TYPE_BOOST.get(source_type, DEFAULT_SOURCE_BOOST)

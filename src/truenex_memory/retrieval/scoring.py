"""BM25 keyword scoring for truenex-memory retrieval."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


SOURCE_TYPE_BOOST: dict[str, float] = {
    "project_docs": 1.0,
    # Past session transcripts are not verified facts: letting them compete
    # at full weight with authoritative documents creates a self-confirmation
    # loop (an agent retrieves things an agent said, not checked evidence).
    # They stay retrievable, but rank below documents.
    "agent_session": 0.5,
}
DEFAULT_SOURCE_BOOST = 0.85


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words (Unicode-aware)."""
    return re.findall(r"\w+", text.lower())


def tokenize_set(text: str) -> set[str]:
    """Return unique lowercase tokens from text."""
    return set(tokenize(text))


# Function words carry no retrieval signal but inflate any overlap ratio
# computed over raw tokens. Measured on real Italian questions: "cos'e il
# source ledger e a cosa serve" tokenizes to 8 terms of which 5 —
# a, cos, cosa, e, il — are function words, so a memory containing only
# those cleared a 0.5 overlap gate with zero topical relevance. Both
# languages are needed: this store holds Italian prose and English code.
STOPWORDS: frozenset[str] = frozenset({
    # Italian
    "a", "ad", "ai", "al", "alla", "alle", "allo", "anche", "che", "chi",
    "ci", "coi", "col", "come", "con", "cos", "cosa", "cui", "da", "dai",
    "dal", "dalla", "degli", "dei", "del", "della", "delle", "dello", "di",
    "e", "ed", "gli", "ha", "hai", "hanno", "ho", "i", "il", "in", "la",
    "le", "lo", "ma", "mi", "ne", "nel", "nella", "no", "non", "o", "per",
    "piu", "qual", "quale", "quali", "quando", "quanto", "quello", "questo",
    "sono", "su", "sui", "sul", "sulla", "te", "ti", "tra", "tu", "un",
    "una", "uno", "va", "vi", "si", "se", "sia", "essere", "fa", "fare",
    "dove", "perche", "mio", "mia", "suo", "sua",
    # English
    "about", "all", "an", "and", "any", "are", "as", "at",
    "be", "been", "but", "by", "can", "do", "does", "for", "from", "get",
    "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "me",
    "my", "no", "not", "of", "on", "or", "our", "so", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "to", "up",
    "use", "used", "was", "we", "what", "when", "where", "which", "who",
    "why", "will", "with", "you", "your",
    # Words that describe the ACT of asking rather than its subject. These
    # matter more than ordinary function words here, because the store
    # contains third-party Italian localization files: measured, "vai a
    # vedere le informazioni sul progetto meddesk" returned WinMerge's
    # ReadMe-Italian.txt at rank 1 and PowerPlatform's Italian strings at
    # rank 3, matching on vai/vedere/informazioni/progetto. With these
    # removed, all four phrasings of that question return 5/5 relevant.
    "aiutami", "circa", "conosci", "dammi", "dimmi", "guarda", "guardare",
    "informazione", "informazioni", "mostra", "mostrami", "parlami",
    "potresti", "progetti", "progetto", "puoi", "qualcosa", "raccontami",
    "riguardo", "sai", "sapere", "spiega", "spiegami", "tutte", "tutti",
    "tutto", "vai", "vedere", "vedi", "voglio", "vorrei",
    "anything", "everything", "explain", "give", "info", "information",
    "know", "need", "please", "project", "show", "tell", "want",
})


def content_tokens_from(tokens: set[str]) -> set[str]:
    """Drop function words from an already-tokenized set.

    Falls back to the input when it is nothing but function words, so a
    degenerate query still matches something rather than silently
    returning no results.
    """
    content = tokens - STOPWORDS
    return content or tokens


def content_tokens(text: str) -> set[str]:
    """Unique lowercase tokens of *text* with function words removed."""
    return content_tokens_from(tokenize_set(text))


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


def document_frequencies(conn, tokens: set[str]) -> dict[str, int]:
    """Corpus document frequency for each token, via FTS5's vocab table.

    `fts5vocab` is created as a TEMP table, so this needs no schema change
    and no migration. Measured on the live store (201k chunks): 1 ms to
    create the table, 0.22 ms per term — negligible against a ~330 ms query.

    Returns an empty dict when the index or the vocab table is unavailable,
    so every caller must treat "no data" as "do not apply IDF rules" rather
    than as "every token is infinitely rare".
    """

    if not tokens:
        return {}
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.truenex_fts_vocab "
            "USING fts5vocab('main', 'chunks_fts', 'row')"
        )
    except Exception:  # pragma: no cover - index absent or not FTS5
        return {}

    frequencies: dict[str, int] = {}
    for token in tokens:
        try:
            row = conn.execute(
                "SELECT doc FROM temp.truenex_fts_vocab WHERE term = ?", (token,)
            ).fetchone()
        except Exception:  # pragma: no cover
            return {}
        frequencies[token] = int(row[0]) if row and row[0] is not None else 0
    return frequencies


def most_informative_tokens(frequencies: dict[str, int]) -> set[str]:
    """The rarest tokens in the corpus — the ones a match must not miss.

    Rarity is the whole signal: on the live store `release` appears in
    10,195 chunks and `bump` in 373, so a note sharing only "release" and
    "versione" with the query "passi per fare una release e il bump di
    versione" is topically unrelated while clearing a plain 0.5 overlap
    ratio. Tokens absent from the corpus (df 0) are NOT treated as
    maximally rare: a typo would otherwise become the one term everything
    has to match.
    """

    present = {token: df for token, df in frequencies.items() if df > 0}
    if not present:
        return set()
    rarest = min(present.values())
    return {token for token, df in present.items() if df == rarest}


def source_boost(source_type: str | None) -> float:
    """Return the score multiplier for a given source_type."""
    if source_type is None:
        return DEFAULT_SOURCE_BOOST
    return SOURCE_TYPE_BOOST.get(source_type, DEFAULT_SOURCE_BOOST)

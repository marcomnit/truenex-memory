"""The rarest-term rule on the memory gate.

The plain overlap ratio treats every content token as equally telling, so on
a short question two generic words carry a memory to rank 1. Measured on the
live store: "quali sono i passi per fare una release e il bump di versione"
has four content terms, and a memory about ANOTHER project sharing only
`release` (df 10,195) and `versione` (df 1,000) cleared 2/4 = 0.5 and took the
top slot while the answering document sat at rank 6. The term that makes the
question specific is `bump` (df 373).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.retrieval.fusion import MEMORY_GATE_NEAR_VERBATIM_OVERLAP
from truenex_memory.retrieval.scoring import (
    document_frequencies,
    most_informative_tokens,
)
from truenex_memory.store.models import SearchHit
from truenex_memory.store.repository import _require_most_informative_token


class _StubConnection:
    """Minimal stand-in for the sqlite3 connection the gate consults.

    Answers the vocab-table creation and one document-frequency lookup per
    term, so the rule's logic can be tested without an FTS index.
    """

    def __init__(self, frequencies: dict[str, int] | None, *, fail: bool = False):
        self.frequencies = frequencies or {}
        self.fail = fail
        self.queries = 0

    def execute(self, sql: str, params: tuple = ()):  # noqa: D102
        if self.fail:
            raise RuntimeError("no FTS index")
        if sql.lstrip().upper().startswith("CREATE"):
            return self
        self.queries += 1
        term = params[0]
        value = self.frequencies.get(term)
        return _StubCursor(None if value is None else (value,))


class _StubCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


def _memory(title: str, content: str, score: float) -> SearchHit:
    return SearchHit(
        title=title,
        content=content,
        source_path=None,
        heading_path=None,
        memory_type="decision",
        status="active",
        score=score,
    )


# ── most_informative_tokens ────────────────────────────────────────────────

def test_rarest_token_is_the_one_with_the_lowest_document_frequency() -> None:
    frequencies = {"passi": 5000, "release": 10195, "bump": 373, "versione": 1000}

    assert most_informative_tokens(frequencies) == {"bump"}


def test_ties_on_rarity_all_count() -> None:
    """With a tie, matching any of the rarest terms is enough.

    Requiring all of them would reject a memory that is genuinely about one
    of two equally specific aspects of the question.
    """

    assert most_informative_tokens({"a": 10, "b": 10, "c": 500}) == {"a", "b"}


def test_tokens_absent_from_the_corpus_are_not_treated_as_rarest() -> None:
    """A typo must not become the term everything has to match.

    `df == 0` means the token appears nowhere, so it carries no evidence at
    all; treating it as maximally rare would empty the memory branch on
    every misspelled query.
    """

    assert most_informative_tokens({"typoo": 0, "doctor": 236}) == {"doctor"}


def test_no_usable_frequencies_yields_no_requirement() -> None:
    assert most_informative_tokens({}) == set()
    assert most_informative_tokens({"a": 0, "b": 0}) == set()


# ── document_frequencies ──────────────────────────────────────────────────

def test_document_frequencies_reads_one_row_per_term() -> None:
    conn = _StubConnection({"doctor": 236, "comando": 815})

    result = document_frequencies(conn, {"doctor", "comando"})

    assert result == {"doctor": 236, "comando": 815}
    assert conn.queries == 2


def test_unknown_term_counts_as_absent_not_missing() -> None:
    conn = _StubConnection({"doctor": 236})

    assert document_frequencies(conn, {"doctor", "zzz"}) == {"doctor": 236, "zzz": 0}


def test_document_frequencies_degrade_to_empty_without_an_index() -> None:
    """No index must mean "skip the IDF rules", never "everything is rare"."""

    assert document_frequencies(_StubConnection(None, fail=True), {"a", "b"}) == {}


def test_document_frequencies_of_nothing() -> None:
    assert document_frequencies(_StubConnection({}), set()) == {}


# ── the gate itself ───────────────────────────────────────────────────────

_RELEASE_QUERY = {"passi", "release", "bump", "versione"}
_RELEASE_FREQUENCIES = {"passi": 5000, "release": 10195, "bump": 373, "versione": 1000}


def test_memory_missing_the_rarest_term_is_dropped() -> None:
    """The live failure, reproduced: generic overlap is not topicality."""

    conn = _StubConnection(_RELEASE_FREQUENCIES)
    off_topic = _memory(
        "MedDesk archivio clinico",
        "note sulla release e sulla versione del vault clinico",
        0.5,
    )
    on_topic = _memory(
        "Release di truenex-memory",
        "i passi sono: bump della versione, changelog, tag",
        0.75,
    )

    kept = _require_most_informative_token(conn, [off_topic, on_topic], _RELEASE_QUERY)

    assert kept == [on_topic]


def test_near_verbatim_memory_bypasses_the_rule() -> None:
    """Curated recall cannot regress.

    Every memory-recall eval case quotes its target almost verbatim over
    7-16 content terms, so the bypass is what makes this rule safe to apply
    at all.
    """

    query = {f"t{i}" for i in range(10)} | {"rarissimo"}
    frequencies = {token: 5000 for token in query}
    frequencies["rarissimo"] = 3
    conn = _StubConnection(frequencies)
    # Shares 10 of 11 terms but happens to miss the rarest one.
    almost_verbatim = _memory(
        "quasi letterale", " ".join(f"t{i}" for i in range(10)),
        MEMORY_GATE_NEAR_VERBATIM_OVERLAP,
    )

    assert _require_most_informative_token(conn, [almost_verbatim], query) == [
        almost_verbatim
    ]


def test_rule_is_skipped_when_no_memory_carries_the_rarest_term() -> None:
    """Better the old behaviour than an empty branch.

    If the rarest term of the query occurs in no memory at all, applying the
    rule would silently discard every candidate — so it is not applied.
    """

    conn = _StubConnection(_RELEASE_FREQUENCIES)
    a = _memory("a", "release e versione", 0.5)
    b = _memory("b", "passi e release", 0.5)

    assert _require_most_informative_token(conn, [a, b], _RELEASE_QUERY) == [a, b]


def test_relevant_memory_missing_the_rarest_term_is_still_dropped() -> None:
    """The case neither escape hatch covers, pinned as a known limitation.

    A genuinely relevant memory with moderate overlap that happens not to
    contain the rarest term IS dropped, while an irrelevant one that
    contains it survives. The rule trades this false negative for the far
    more common false positive it removes; this test exists so the
    trade-off is explicit and a future change to the rule has to confront
    it rather than discover it.
    """

    conn = _StubConnection(_RELEASE_FREQUENCIES)
    relevant_but_missing = _memory(
        "Procedura di rilascio",
        "i passi del rilascio e la versione da pubblicare",  # no `bump`
        0.75,
    )
    irrelevant_but_matching = _memory(
        "Nota sparsa", "un bump di dipendenze in un progetto diverso", 0.5
    )

    kept = _require_most_informative_token(
        conn, [relevant_but_missing, irrelevant_but_matching], _RELEASE_QUERY
    )

    assert kept == [irrelevant_but_matching]


def test_single_term_query_is_left_alone() -> None:
    """With one content term the existing overlap gate already requires it."""

    conn = _StubConnection({"doctor": 236})
    hit = _memory("nota", "qualcosa su doctor", 1.0)

    assert _require_most_informative_token(conn, [hit], {"doctor"}) == [hit]
    assert conn.queries == 0


def test_no_memories_no_work() -> None:
    conn = _StubConnection(_RELEASE_FREQUENCIES)

    assert _require_most_informative_token(conn, [], _RELEASE_QUERY) == []
    assert conn.queries == 0


def test_missing_index_leaves_every_memory_in_place() -> None:
    conn = _StubConnection(None, fail=True)
    hit = _memory("nota", "release e versione", 0.5)

    assert _require_most_informative_token(conn, [hit], _RELEASE_QUERY) == [hit]

"""Italian-to-English query expansion for the document branch.

The store holds Italian prose alongside English source code and
English-titled documentation, but the lexical ranker matches literal
tokens with no stemmer and no cross-language bridge. Measured on 14
realistic questions: asked in Italian, 5 of 14 retrieved a document that
answers; asked in English, 8 of 14. The gap is purely lexical — the same
documents, unreachable through Italian words.

This module adds the English equivalents of domain terms to the token set
used for CHUNK search only. It must not be applied to the memory branch:
memory scores are an overlap ratio, so expanding the query would move
their denominator and silently shift the memory relevance gate.

FTS5 here is configured `unicode61 remove_diacritics 2` with no stemmer
(see store/sqlite.py), so morphology is not bridged even within Italian —
hence the plural and conjugated forms are listed explicitly rather than
relying on stemming.
"""

from __future__ import annotations

# Italian term -> English equivalents to add. Keep entries specific:
# a high-document-frequency term buys noise, and FTS5 OR has no per-term
# weighting, so an expansion token cannot be down-weighted afterwards.
GLOSSARY: dict[str, tuple[str, ...]] = {
    "aggiornamento": ("update", "upgrade"),
    "aggiorna": ("update",),
    "aggiunge": ("add", "insert"),
    "aggiungere": ("add", "insert"),
    "avvio": ("startup", "start"),
    "cartella": ("directory", "folder"),
    "catalogo": ("catalog",),
    "chiave": ("key",),
    "comando": ("command",),
    "comandi": ("commands", "command"),
    "configura": ("configure", "configuration", "config"),
    "configurare": ("configure", "configuration", "config"),
    "configurazione": ("configuration", "config"),
    "diagnostica": ("diagnostics", "diagnostic"),
    "documento": ("document",),
    "documenti": ("documents", "document"),
    "errore": ("error",),
    "errori": ("errors", "error"),
    "esporta": ("export",),
    "esportare": ("export",),
    "fonte": ("source",),
    "fonti": ("sources", "source"),
    "globale": ("global",),
    "importa": ("import",),
    "incorporare": ("embed", "embedding"),
    "indicizza": ("index", "indexing"),
    "indicizzazione": ("indexing", "index"),
    "invalida": ("invalidate", "invalidation", "invalidated"),
    "invalidazione": ("invalidation", "invalidate"),
    "licenza": ("license", "licence"),
    "memoria": ("memory",),
    "migrano": ("migrate", "migration", "migrations"),
    "migrare": ("migrate", "migration", "migrations"),
    "migrazione": ("migration", "migrate"),
    "orchestratore": ("orchestrator", "orchestration"),
    "passi": ("steps", "step"),
    "percorso": ("path",),
    "reimporta": ("import",),
    "ricerca": ("search",),
    "ricorsivo": ("recursive", "recursion"),
    "schemi": ("schema", "schemas"),
    "sessione": ("session",),
    "sessioni": ("sessions", "session"),
    "strutturati": ("structure", "stored", "layout"),
    "struttura": ("structure", "layout"),
    "utente": ("user",),
    "verifica": ("verify", "check", "validation"),
    "versione": ("version",),
    "vettoriale": ("vector",),
    "vettori": ("vectors", "vector"),
}

# Minimum length for prefix matching. Short tokens produce huge candidate
# sets on a 497k-chunk index for no gain.
_MIN_PREFIX_LENGTH = 6


def expand_for_chunks(tokens: set[str]) -> set[str]:
    """Build the chunk-search term set: drop function words, add English.

    Function words must go before anything else. Candidate selection is
    `ORDER BY bm25(...) LIMIT 100`, and bm25 weights the heading column
    2x, so a file whose *title* contains Italian function words is
    amplified into the candidate list. Measured on "cos'e il source
    ledger e a cosa serve": 60 of the 100 candidate slots were taken by
    four files, 24 of them by DUAL_MODEL_CHI_FA_COSA_E_LOG_ENGINE.md,
    matching on `cosa` and `e` — while global_refresh.py, which holds 32
    chunks containing both `source` and `ledger`, never entered the
    candidate list at all. That is a recall failure, not a ranking one.

    The surviving Italian content terms are kept alongside their English
    equivalents: the corpus holds Italian documents too, and dropping
    them would trade one language's recall for the other's.
    """
    from truenex_memory.retrieval.scoring import content_tokens_from

    terms = content_tokens_from(tokens)
    expanded = set(terms)
    for token in terms:
        expanded.update(GLOSSARY.get(token, ()))
    return expanded

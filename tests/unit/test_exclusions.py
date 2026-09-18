"""Esclusioni di indicizzazione — regressione per il rumore di `tool-results`.

Trovato con una domanda vera sull'archivio reale dell'utente (2026-09-18):
`memory_search` restituiva dump grezzi di chiamate a tool
(`~/.claude/projects/<slug>/<sessione>/tool-results/*.txt` — output SQL,
grep) al posto del documento che rispondeva davvero alla domanda, perche'
quella cartella non era mai stata esclusa dall'indicizzazione.

Una prima versione di questo fix escludeva l'intera cartella `.claude`. Un
dry-run del comando di pulizia contro l'archivio reale, lo stesso giorno,
ha mostrato che avrebbe cancellato 4690 documenti — quasi tutti trascrizioni
di sessione (`.claude/projects/*.jsonl`), che questo progetto indicizza DI
PROPOSITO (13.299 righe `agent_session` nel ledger). Il test seguente
blocca quella regressione: solo `tool-results` va escluso.
"""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.exclusions import should_exclude


def test_tool_results_under_a_claude_session_is_excluded() -> None:
    path = (
        Path("/home/user")
        / ".claude" / "projects" / "slug" / "session-id" / "tool-results" / "dump.txt"
    )

    assert should_exclude(path, root=Path("/home/user"))


def test_tool_results_dump_is_excluded_even_without_a_claude_ancestor() -> None:
    """Difesa in profondita': lo stesso genere di scarto puo' comparire un
    livello sotto una radice che non e' `.claude` (un altro client)."""

    path = Path("/home/user/project") / "somewhere" / "tool-results" / "call_00_test.txt"

    assert should_exclude(path, root=Path("/home/user/project"))


def test_claude_session_transcripts_are_not_excluded() -> None:
    """Regressione: questi .jsonl sono un source_type indicizzato apposta
    (`agent_session`), non uno scarto da tool-results."""

    path = Path("/home/user") / ".claude" / "projects" / "slug" / "session-id.jsonl"

    assert not should_exclude(path, root=Path("/home/user"))


def test_a_real_document_next_to_the_noise_is_not_excluded() -> None:
    path = Path("/home/user/project") / "README.md"

    assert not should_exclude(path, root=Path("/home/user/project"))

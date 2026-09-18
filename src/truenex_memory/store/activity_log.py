"""Traccia automatica di ogni chiamata servita dal server MCP stdio.

## Perche' non basta task_open/task_step_add

Quegli strumenti esistono da prima e restano scritti a mano dall'agente: se
l'agente non li chiama — perche' se ne dimentica, perche' il client non
segue il profilo, perche' la sessione e' breve — la cronologia semplicemente
non esiste. E' la stessa fragilita' di ``MEMORY.md`` scritto a mano, solo
spostata di un livello: la lettura sarebbe automatica, ma la SCRITTURA
resterebbe affidata alla disciplina di chi chiama. Un secondo parere
indipendente (2026-09-18, tre modelli diversi) e' stato netto su questo
punto: se non e' l'infrastruttura a scrivere, il sistema non e' affidabile.

## Perche' qui e' diverso

``mcp/server.py::_call_tool`` e' l'UNICO punto per cui passa ogni chiamata
`tools/call`, qualunque sia il tool e qualunque sia il client (Claude, Codex,
Cursor, Kimi): e' il solo posto dove una registrazione non dipende dalla
cooperazione di chi chiama. Registrare li', una riga per chiamata, rende la
domanda «cosa e' successo per ultimo in questo progetto» rispondibile
sempre, non solo quando l'agente si e' ricordato di dirlo esplicitamente.

## Perche' non e' una tabella nello schema versionato

Additiva e creata al bisogno (``CREATE TABLE IF NOT EXISTS``), come
``source_attribution`` in ``projects/store.py``: chi legge e non la trova sa
che nessuna chiamata e' ancora stata registrata, invece di far fallire
l'apertura di un archivio esistente con una colonna mancante.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TABLE = "activity_log"

# Oltre questa lunghezza il riassunto viene tagliato: questa tabella serve a
# ricostruire UNA cronologia leggibile, non a duplicare il contenuto che
# memory_search e memory_get gia' custodiscono per intero.
MAX_SUMMARY_CHARS = 200


def ensure_table(conn: sqlite3.Connection) -> None:
    """Crea la tabella se non c'e'. Idempotente, non tocca nient'altro."""

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  TEXT NOT NULL,
            tool_name   TEXT NOT NULL,
            summary     TEXT,
            ok          INTEGER NOT NULL,
            created_at  TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_project_time "
        f"ON {TABLE} (project_id, created_at)"
    )


def record(
    db_path: Path,
    *,
    project_id: str,
    tool_name: str,
    summary: str | None,
    ok: bool,
) -> None:
    """Registra una chiamata. Chiamato dal dispatcher per OGNI tool, sempre.

    Il chiamante lo racchiude in un try/except a parte: una riga di
    cronologia mancata non e' mai un motivo per far fallire una risposta.
    """

    if summary and len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[: MAX_SUMMARY_CHARS - 1] + "…"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_table(conn)
        conn.execute(
            f"INSERT INTO {TABLE} (project_id, tool_name, summary, ok, created_at) "
            f"VALUES (?, ?, ?, ?, ?)",
            (project_id, tool_name, summary, 1 if ok else 0, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def recent(db_path: Path, *, project_id: str, limit: int = 20) -> list[dict[str, object]]:
    """Le ultime *limit* chiamate per questo progetto, piu' recenti prima.

    Un archivio senza la tabella (nessuna chiamata mai registrata, es. un
    progetto appena aperto) torna una lista vuota — l'assenza si rappresenta
    con l'assenza, non con un errore.
    """

    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()
        if exists is None:
            return []
        rows = conn.execute(
            f"SELECT tool_name, summary, ok, created_at FROM {TABLE} "
            f"WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

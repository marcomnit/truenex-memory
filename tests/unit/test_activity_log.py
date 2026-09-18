"""Cronologia automatica delle chiamate MCP, alla base di `memory_resume`.

Vedi il docstring del modulo per il motivo per cui questa scrittura non deve
dipendere dalla disciplina dell'agente: qui si verifica solo il contratto di
`record`/`recent`, non il punto di innesto nel dispatcher (coperto in
`test_mcp_server.py`).
"""

from __future__ import annotations

from pathlib import Path

from truenex_memory.store.activity_log import record, recent


def test_a_fresh_project_has_no_activity(tmp_path: Path) -> None:
    assert recent(tmp_path / "truenex_memory.db", project_id="p") == []


def test_recorded_calls_come_back_most_recent_first(tmp_path: Path) -> None:
    db_path = tmp_path / "truenex_memory.db"

    record(db_path, project_id="p", tool_name="memory_add", summary="uno", ok=True)
    record(db_path, project_id="p", tool_name="memory_search", summary="due", ok=True)

    entries = recent(db_path, project_id="p", limit=10)

    assert [e["tool_name"] for e in entries] == ["memory_search", "memory_add"]


def test_projects_do_not_see_each_others_activity(tmp_path: Path) -> None:
    db_path = tmp_path / "truenex_memory.db"

    record(db_path, project_id="a", tool_name="memory_add", summary="per A", ok=True)
    record(db_path, project_id="b", tool_name="memory_add", summary="per B", ok=True)

    assert [e["summary"] for e in recent(db_path, project_id="a")] == ["per A"]


def test_a_failed_call_is_recorded_too(tmp_path: Path) -> None:
    """Un errore fa parte della cronologia: sapere che qualcosa e' fallito
    l'ultima volta e' spesso proprio la risposta a "dove eravamo rimasti"."""

    db_path = tmp_path / "truenex_memory.db"

    record(db_path, project_id="p", tool_name="memory_graph", summary="error: not found", ok=False)

    entry = recent(db_path, project_id="p")[0]
    assert entry["ok"] == 0


def test_long_summaries_are_truncated(tmp_path: Path) -> None:
    db_path = tmp_path / "truenex_memory.db"

    record(db_path, project_id="p", tool_name="memory_add", summary="x" * 500, ok=True)

    entry = recent(db_path, project_id="p")[0]
    assert len(entry["summary"]) <= 200

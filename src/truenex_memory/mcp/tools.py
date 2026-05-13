"""Local MCP-compatible tool functions."""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.memory_service import MemoryService
from truenex_memory.ingestion.global_context import build_project_context
from truenex_memory.ingestion.global_status import build_global_status
from truenex_memory.retrieval.result import search_payload


def memory_search(query: str, top_k: int = 5, *, project_root: Path | str = ".") -> dict[str, object]:
    """Search local memory using the stable MCP result shape."""

    service = MemoryService(project_root)
    results = service.search(query, top_k=top_k)
    return search_payload(query, results, trace_id=service.last_trace_id)


def memory_add(
    content: str,
    memory_type: str = "note",
    *,
    project_root: Path | str = ".",
) -> dict[str, object]:
    """Add a local memory node."""

    service = MemoryService(project_root)
    memory_id = service.add(content, memory_type=memory_type)
    return {"id": memory_id, "status": "active", "memory_type": memory_type}


def global_status(
    home: str | Path | None = None,
    catalog: str | Path | None = None,
    db: str | Path | None = None,
) -> dict[str, object]:
    """Read-only global status report for the Truenex Memory global store."""

    _home = Path(home) if home else Path.home()
    catalog_path = Path(catalog) if catalog else _home / ".truenex-memory" / "sources.json"
    db_path = Path(db) if db else _home / ".truenex-memory" / "truenex_memory.db"

    report = build_global_status(catalog_path=catalog_path, db_path=db_path)
    return report.to_dict()


def global_project_context(
    project: str,
    home: str | Path | None = None,
    catalog: str | Path | None = None,
    db: str | Path | None = None,
    limit: int = 20,
) -> dict[str, object]:
    """Read-only project context report for a project in the global store."""

    if not isinstance(project, str) or not project.strip():
        raise ValueError("project must be a non-empty string")
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        raise ValueError("limit must be an integer between 1 and 100")

    _home = Path(home) if home else Path.home()
    catalog_path = Path(catalog) if catalog else _home / ".truenex-memory" / "sources.json"
    db_path = Path(db) if db else _home / ".truenex-memory" / "truenex_memory.db"

    report = build_project_context(
        project_query=project,
        catalog_path=catalog_path,
        db_path=db_path,
        limit=limit,
    )
    return report.to_dict()


from truenex_memory.store.task_store import TaskStore, TASK_TYPES, BRAIN_JUDGMENTS


def _default_task_store(db: str | None = None) -> TaskStore:
    db_path = Path(db) if db else Path.home() / ".truenex-memory" / "truenex_memory.db"
    return TaskStore(db_path)


def task_open(
    title: str,
    task_type: str = "feature",
    *,
    project: str | None = None,
    agent_session_id: str | None = None,
    db: str | None = None,
) -> dict[str, object]:
    """Open a new task record in the adaptive pipeline."""
    if task_type not in TASK_TYPES:
        raise ValueError(f"task_type must be one of {sorted(TASK_TYPES)}")
    task_id = _default_task_store(db).task_open(title, task_type, project=project, agent_session_id=agent_session_id)
    return {"task_id": task_id, "status": "open"}


def task_step_add(
    task_id: str,
    *,
    prompt_used: str | None = None,
    output: str | None = None,
    brain_judgment: str | None = None,
    tokens_used: int | None = None,
    duration_s: float | None = None,
    model_used: str | None = None,
    db: str | None = None,
) -> dict[str, object]:
    """Add a step record to an open task."""
    if brain_judgment is not None and brain_judgment not in BRAIN_JUDGMENTS:
        raise ValueError(f"brain_judgment must be one of {sorted(BRAIN_JUDGMENTS)}")
    step_id = _default_task_store(db).step_add(
        task_id, prompt_used=prompt_used, output=output, brain_judgment=brain_judgment,
        tokens_used=tokens_used, duration_s=duration_s, model_used=model_used,
    )
    return {"step_id": step_id, "task_id": task_id}


def task_close(
    task_id: str,
    *,
    human_outcome: int | None = None,
    human_comment: str | None = None,
    db: str | None = None,
) -> dict[str, object]:
    """Close a task. Provide human_outcome (1/0/-1) or omit for unrated."""
    if human_outcome is not None and human_outcome not in (1, 0, -1):
        raise ValueError("human_outcome must be 1, 0, or -1")
    record = _default_task_store(db).task_close(task_id, human_outcome=human_outcome, human_comment=human_comment)
    return {"task_id": record.task_id, "status": record.status, "human_outcome": record.human_outcome, "closed_at": record.closed_at}

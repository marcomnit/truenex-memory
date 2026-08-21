"""Local MCP-compatible tool functions."""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.memory_service import MemoryService
from truenex_memory.ingestion.global_context import build_project_context
from truenex_memory.ingestion.global_status import build_global_status
from truenex_memory.retrieval.result import search_payload


def memory_graph(
    target: str,
    *,
    scope: str | None = None,
    limit: int | None = None,
    project_root: Path | str = ".",
) -> dict[str, object]:
    """Structural facts about one function, class or file from the code graph.

    A different door from `memory_search`, deliberately. Search answers what
    was written and decided; this answers how the code is wired: who calls a
    function, what it calls, which tests exercise it, and which docstring
    explains it. Those are relations read from the source by a parser, so they
    are either correct or absent — never a plausible near-match.

    Kept out of the ranking on purpose. Every attempt to feed extra candidates
    into the fused ranking lost cases (measured 2026-08-21: a cross-encoder
    over the candidate union, a dense fallback route, and query-term pruning
    all cost more than they gained). A separate tool adds a capability without
    touching a ranking that took a day to stabilise.

    Requires `truenex-mem graph build <path>` to have run for that tree.
    """

    from truenex_memory.graph import (
        CACHE_VERSION,
        EXPLAIN_GROUP_LIMIT,
        ensure_current,
        default_cache_dirs,
        explain_entity,
        find_cached_graph,
        graphify_available,
    )
    from truenex_memory.core.config import resolve_project_config

    if limit is None:
        limit = EXPLAIN_GROUP_LIMIT
    config = resolve_project_config(project_root)
    cache_dirs = default_cache_dirs(config.db_path)
    graphs = []
    for directory in cache_dirs:
        if directory.is_dir():
            graphs.extend(sorted(directory.glob("*.json")))
    if not graphs:
        return {
            "target": target,
            "error": "no code graph built yet",
            "hint": "run: truenex-mem graph build <project path>",
            "backend_installed": graphify_available(),
        }

    # Il grafo giusto e' quello la cui radice contiene lo scope richiesto;
    # senza scope si prende quello che nomina il bersaglio.
    from truenex_memory.graph import FileGraph
    import json as _json

    best: FileGraph | None = None
    stale: list[str] = []
    for entry in graphs:
        try:
            data = _json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("cache_version") != CACHE_VERSION:
            # Un grafo di una versione precedente non ha il livello entita' e
            # risponderebbe "non trovato" a qualunque funzione.
            stale.append(str(data.get("root", entry.name)))
            continue
        graph = FileGraph.from_dict(data)
        if scope and scope.replace("\\", "/").lower() not in graph.root.lower():
            continue
        if explain_entity(graph, target, limit=1)["matched"]:
            best = graph
            break
        if best is None:
            best = graph
    if best is None:
        if stale:
            return {
                "target": target,
                "error": "the code graph for these trees predates the entity level",
                "hint": "rebuild with: truenex-mem graph build <path>",
                "stale": stale,
            }
        return {"target": target, "error": "no code graph matches that scope"}

    result = explain_entity(best, target, limit=limit)
    result["project"] = best.root
    # Il grafo e' una fotografia. Se i sorgenti sono cambiati da quando e' stato
    # costruito, questa risposta parla del passato: dirlo qui e' l'unico modo
    # perche' l'agente lo sappia senza che una persona debba ricordarselo.
    # La freschezza viene verificata e, se serve, rimediata qui: in libreria e
    # non nella configurazione di un client, perche' questa porta la usano
    # Claude, Codex, Cursor e chiunque parli MCP.
    freshness = ensure_current(best, cache_dirs[0])
    if freshness.get("stale"):
        result["stale"] = {
            **freshness.get("counts", {}),
            "rebuild": freshness["rebuild"],
            "examples": (
                freshness.get("changed", [])
                + freshness.get("missing", [])
                + freshness.get("tree", [])
            )[:5],
        }
        if freshness.get("hint"):
            result["stale"]["hint"] = freshness["hint"]
    elif freshness.get("stale") is None:
        result["stale"] = {"unknown": freshness.get("reason"), "rebuild": freshness["rebuild"]}
    return result


def memory_search(
    query: str,
    top_k: int = 5,
    *,
    full_content: bool = False,
    project_root: Path | str = ".",
    scope: str | None = None,
) -> dict[str, object]:
    """Search local memory using the stable MCP result shape.

    Defaults to excerpts because this surface is consumed by agents that
    pay per token: verbatim bodies made a five-result response run to
    several kilobytes even when nothing in it was relevant. Each result
    still carries ``document_id`` / ``memory_id``, so ``memory_get``
    resolves anything worth reading in full.
    """

    from truenex_memory.core.config import resolve_project_config

    service = MemoryService(project_root)
    diagnostics: dict[str, object] = {}
    results = service.search(query, top_k=top_k, scope=scope, diagnostics=diagnostics)
    payload = search_payload(
        query,
        results,
        trace_id=service.last_trace_id,
        full_content=full_content,
    )
    # La provenienza viaggia con la risposta, non nel log del server.
    #
    # Due modi di sbagliare lo scope restavano invisibili a chi chiama: uno che
    # non corrisponde a niente fa ricadere la ricerca sull'intero corpus (utile,
    # ma taciuto), e uno che indica un progetto esistente ma sbagliato
    # restituisce documenti coerenti e di un altro progetto. Con `answered_from`
    # nella risposta lo scarto fra cio' che si e' chiesto e cio' che si e'
    # ottenuto e' leggibile subito, invece di essere un compito di attenzione
    # affidato a chi cerca.
    if scope:
        # Un archivio di progetto contiene solo quel progetto, e registra i
        # percorsi RELATIVI alla sua radice: nessun percorso contiene il nome
        # del progetto, quindi lo scope non corrisponde mai e la ricerca
        # ricade sull'intero archivio — che pero' e' esattamente il progetto
        # richiesto. Segnalarlo come «risposta NON ristretta» sarebbe un
        # allarme falso, e un allarme falso insegna a ignorare quelli veri.
        # Globale e' l'archivio nella cartella utente; qualunque altro e' di
        # progetto. Una prima versione confrontava data_dir con project_root, e
        # dichiarava «di progetto» anche l'archivio globale ogni volta che
        # qualcuno cercava con project_root nella home — cioe' proprio il caso
        # in cui l'avviso serve.
        config = resolve_project_config(project_root)
        global_dir = (Path.home() / ".truenex-memory").resolve()
        project_store = config.data_dir.resolve() != global_dir
        payload["scope"] = {
            "requested": scope,
            "applied": diagnostics.get("scope_applied"),
            "answered_from": diagnostics.get("answered_from"),
            "store": "project" if project_store else "global",
        }
        if project_store:
            payload["scope"]["note"] = (
                "archivio di questo solo progetto: lo scope non restringe niente "
                "perche' non c'e' altro da escludere"
            )
        elif diagnostics.get("scope_fell_back"):
            payload["scope"]["note"] = (
                f"'{scope}' non ha prodotto candidati nell'archivio globale: "
                "risposta cercata su TUTTI i progetti, non ristretta. Se il nome "
                "e' sbagliato, la risposta puo' venire da un altro progetto — "
                "guarda 'answered_from'"
            )
    return payload


def memory_get(
    document_id: str | None = None,
    memory_id: str | None = None,
    *,
    project_root: Path | str = ".",
) -> dict[str, object]:
    """Resolve one search result to its full text.

    Takes either id emitted by ``memory_search``. Exactly one is required:
    accepting both silently would hide which one actually answered.
    """

    if bool(document_id) == bool(memory_id):
        return {"error": "provide exactly one of document_id or memory_id"}

    service = MemoryService(project_root)
    repository = service.repository

    if memory_id:
        node = repository.get_memory_node(memory_id)
        if node is None:
            return {"error": "memory not found", "memory_id": memory_id}
        payload: dict[str, object] = {
            "memory_id": node.id,
            "title": node.title,
            "memory_type": node.type,
            "status": node.status,
            "source_path": node.source_path,
            "content_chars": len(node.content or ""),
            "content": node.content,
        }
        if node.superseded_by:
            # A retired note must lead somewhere: without this the reader
            # learns the claim is stale but not what replaced it.
            payload["superseded_by"] = node.superseded_by
            payload["note"] = (
                "This memory has been superseded. Read memory_get on "
                "superseded_by for what currently holds true."
            )
        return payload

    return repository.get_document_text(str(document_id))


def memory_add(
    content: str,
    memory_type: str = "note",
    *,
    supersedes: str | None = None,
    project_root: Path | str = ".",
) -> dict[str, object]:
    """Add a local memory node."""

    service = MemoryService(project_root)
    memory_id = service.add(
        content, memory_type=memory_type, supersedes=supersedes
    )
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

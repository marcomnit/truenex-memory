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

    # Il grafo giusto e' quello la cui radice corrisponde allo scope
    # richiesto; senza scope si prende quello che nomina il bersaglio.
    from truenex_memory.graph import FileGraph
    import json as _json

    loaded: list[FileGraph] = []
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
        loaded.append(FileGraph.from_dict(data))

    candidates = loaded
    if scope:
        # Corrispondenza esatta sul nome dell'ultima cartella prima di
        # quella per sottostringa: "truenex" contenuto sia in
        # "truenex-memory" che in "truenex-engine" sceglieva in silenzio il
        # primo dei due invece di dirlo. Un nome di cartella intero non ha
        # questa ambiguita' nel caso comune, quindi ha la precedenza.
        exact = [g for g in loaded if Path(g.root).name.lower() == scope.strip().lower()]
        if exact:
            candidates = exact
        else:
            scope_norm = scope.replace("\\", "/").strip().lower()
            substring = [g for g in loaded if scope_norm in g.root.replace("\\", "/").lower()]
            distinct_roots = sorted({g.root for g in substring})
            if len(distinct_roots) > 1:
                return {
                    "target": target,
                    "error": f"scope '{scope}' matches {len(distinct_roots)} code graphs",
                    "candidates": distinct_roots,
                    "hint": "re-call memory_graph with one of these roots as `scope`",
                }
            candidates = substring

    best: FileGraph | None = None
    for graph in candidates:
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

    # Ripiego testuale quando la copertura del parser e' nota incompleta.
    #
    # Non solo quando i chiamanti sono zero: un chiamante risolto puo' convivere
    # con altri mancanti, e limitarsi al caso vuoto lascerebbe passare proprio le
    # risposte «plausibili e incomplete», che sono le peggiori.
    #
    # I candidati NON entrano fra i `callers`. Sono due qualita' di verita'
    # diverse — una relazione letta dal parser e una riga di testo compatibile —
    # e mescolarle nello stesso elenco distruggerebbe la fiducia anche in quelle
    # vere. Il contratto per chi legge sta scritto accanto ai dati.
    if (result.get("coverage") or {}).get("incomplete") and result.get("matched"):
        from truenex_memory.graph import text_call_sites

        nome = result["matched"][0].split("::", 1)[-1].lstrip(".")
        candidati = text_call_sites(Path(best.root), nome, files=best.file_set())
        noti = {c["entity"].split("::", 1)[0] for c in result["callers"]}
        result["candidate_callers_from_text"] = {
            "pattern": f"{nome}(",
            # Il contratto per intero sta nella descrizione del tool, che arriva
            # una volta per sessione; qui basta l'etichetta.
            "confidence": "low",
            "source": "text_search",
            "matches": [c for c in candidati if c["file"] not in noti],
        }
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
            # A quale meta' del corpus il filtro e' arrivato. `applied` da solo
            # afferma piu' di quanto sia vero: i chunk dei documenti vengono
            # filtrati, i nodi di memoria no. Il campo si AGGIUNGE invece di
            # cambiare la forma di `applied`, perche' trasformare un booleano
            # pubblico in un oggetto romperebbe chi lo legge oggi.
            "coverage": diagnostics.get("scope_coverage"),
            "answered_from": diagnostics.get("answered_from"),
            "store": "project" if project_store else "global",
        }
        # Le memorie non ristrette sono la ragione piu' probabile per cui
        # `answered_from` elenca progetti che non sono stati chiesti. Dirlo qui
        # evita che chi verifica lo scopra da solo credendo di aver trovato un
        # difetto nuovo — e' un limite conosciuto, non una sorpresa.
        estranei = [
            p for p in (diagnostics.get("answered_from") or []) if scope and p != scope
        ]
        if estranei and diagnostics.get("scope_applied"):
            payload["scope"]["coverage_note"] = (
                "il filtro copre i documenti indicizzati, NON le memorie: una memoria"
                f" pertinente scritta altrove entra comunque. Fuori da '{scope}': "
                + ", ".join(estranei)
            )
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


def memory_resume(
    *,
    limit_activity: int = 20,
    limit_tasks: int = 5,
    limit_memories: int = 10,
    project_root: Path | str = ".",
) -> dict[str, object]:
    """Chronological recap of recent work on THIS project — not a search.

    A different door again from `memory_search` and `memory_graph`, for a
    question neither answers: "what happened last, in what order, what's
    still open". That is a question about RECENCY and STRUCTURE, not about
    relevance to a query — lexical and semantic ranking both optimise for
    "what matches these words", and a diagnosis-then-measurement on this
    project's own corpus (2026-08-21, 53 held-out cases) found dense cosine
    ranking does not surface "what's most recent" any better than chance,
    because recency is not a property of a chunk's text. Asking
    memory_search "where did we leave off" is asking the wrong tool.

    Three sources, each labeled by how much to trust it:

    - ``activity`` — every MCP tool call made against this project's own
      store, logged by the stdio server itself (`mcp/server.py::_call_tool`)
      regardless of which tool was called or which client called it. This
      is the reliable signal: it does not depend on an agent remembering to
      narrate its own work.
    - ``tasks`` — from the adaptive task pipeline (`task_open`/
      `task_step_add`/`task_close`), matched to this project BY NAME
      (the global task store has no path-based link to a project root).
      Best-effort: a task never opened, or opened under a different name,
      will not show up here. Never treat an empty list as "nothing
      happened" — check ``activity`` for that.
    - ``recent_memories`` — this project's own `memory_add` notes/decisions,
      most recent first. As reliable as ``activity``: same local store.

    ``coverage`` names what each source can and cannot promise, so an empty
    or thin recap reads as "little was logged", never as "nothing to
    report" when something in fact happened outside these three sources
    (e.g. work done through a client that bypasses this server, or from
    before this feature existed).
    """

    from truenex_memory.store.activity_log import recent as recent_activity
    from truenex_memory.store.task_store import TaskStore

    service = MemoryService(project_root)
    service.init_project()
    project_id = service.repository.project_id
    project_name = service.config.project_root.name

    activity = recent_activity(service.config.db_path, project_id=project_id, limit=limit_activity)

    # I task vivono nell'archivio GLOBALE (~/.truenex-memory), non in quello
    # locale del progetto: e' dove task_open/task_step_add scrivono davvero
    # (vedi mcp.tools._default_task_store). Leggerli dal db locale — come
    # faceva una prima versione di questa funzione — trovava solo righe
    # residue di un percorso di codice precedente e mai i task appena
    # aperti: un collaudo con una domanda vera, non un test unitario, e' cio'
    # che lo ha mostrato.
    global_db_path = Path.home() / ".truenex-memory" / "truenex_memory.db"
    task_store = TaskStore(global_db_path)
    try:
        matched_tasks = task_store.task_list(project=project_name, limit=limit_tasks)
    except Exception:
        matched_tasks = []
    tasks: list[dict[str, object]] = []
    for task in matched_tasks:
        try:
            steps = task_store.step_list(task.task_id)
        except Exception:
            steps = []
        last_step = steps[-1] if steps else None
        tasks.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status,
                "created_at": task.created_at,
                "closed_at": task.closed_at,
                "human_outcome": task.human_outcome,
                "step_count": len(steps),
                "last_step": (
                    {
                        "brain_judgment": last_step.brain_judgment,
                        "output": (last_step.output or "")[:200],
                        "created_at": last_step.created_at,
                    }
                    if last_step is not None
                    else None
                ),
            }
        )

    memory_nodes = sorted(
        service.list_memory_nodes(status="active"),
        key=lambda node: node.created_at,
        reverse=True,
    )[:limit_memories]
    recent_memories = [
        {
            "memory_id": node.id,
            "memory_type": node.type,
            "title": node.title,
            "created_at": node.created_at,
        }
        for node in memory_nodes
    ]

    return {
        "project": project_name,
        "activity": activity,
        "tasks": tasks,
        "recent_memories": recent_memories,
        "coverage": {
            "activity": (
                "every MCP tool call against this project's own store since this "
                "feature shipped; empty means none were made through this server, "
                "not that nothing happened"
            ),
            "tasks": (
                "matched to this project by NAME against the global task store "
                "(task_open has no path-based link to a project root); a task "
                "opened under a different name will not appear here"
            ),
            "recent_memories": "this project's own memory_add notes/decisions, exact",
        },
    }


def memory_add(
    content: str,
    memory_type: str = "note",
    *,
    supersedes: str | None = None,
    project: str | None = None,
    project_root: Path | str = ".",
    home: str | Path | None = None,
    catalog: str | Path | None = None,
) -> dict[str, object]:
    """Add a local memory node.

    Writes into the project resolved from ``project_root`` (the session's
    cwd) unless ``project`` names a DIFFERENT project — a name, basename, or
    path already confirmed in the global source catalog. Without this, a
    note discovered while working in project A but relevant to project B
    could only ever be written into A, because ``project_root`` tracks the
    session, not the note's subject.
    """

    if project:
        from truenex_memory.ingestion.global_context import resolve_project_root

        _home = Path(home) if home else Path.home()
        catalog_path = Path(catalog) if catalog else _home / ".truenex-memory" / "sources.json"
        root, ambiguous, error = resolve_project_root(project, catalog_path)
        if ambiguous:
            return {
                "error": f"project '{project}' is ambiguous",
                "candidates": ambiguous,
                "hint": "re-call with one of these paths as `project`",
            }
        if root is None:
            return {"error": error or f"project '{project}' not found"}
        project_root = root

    service = MemoryService(project_root)
    memory_id = service.add(
        content, memory_type=memory_type, supersedes=supersedes
    )
    return {
        "id": memory_id,
        "status": "active",
        "memory_type": memory_type,
        "project_root": str(service.config.project_root),
    }


def global_status(
    home: str | Path | None = None,
    catalog: str | Path | None = None,
    db: str | Path | None = None,
) -> dict[str, object]:
    """Global status report for the Truenex Memory global store.

    Reads without mutating the catalog or the database. It DOES, as a side
    effect, start a background reindex when the corpus is older than
    ``TRUENEX_GLOBAL_REFRESH_MAX_AGE_SECONDS`` (default 6h) — the same
    fire-and-forget mechanism ``memory_graph`` already uses for the code
    graph. Without it, ``last_indexed_at`` stays frozen at whatever the last
    manual ``truenex-mem global refresh`` happened to be, which is exactly
    the failure this closes: an agent reading a status report has no way to
    run that command itself, so a purely-manual refresh means the report
    silently goes stale.
    """

    from truenex_memory.ingestion.reactive_refresh import ensure_global_fresh

    _home = Path(home) if home else Path.home()
    catalog_path = Path(catalog) if catalog else _home / ".truenex-memory" / "sources.json"
    db_path = Path(db) if db else _home / ".truenex-memory" / "truenex_memory.db"

    report = build_global_status(catalog_path=catalog_path, db_path=db_path)
    payload = report.to_dict()
    payload["reactive_refresh"] = ensure_global_fresh(
        catalog_path=catalog_path,
        db_path=db_path,
        last_indexed_at=report.last_indexed_at,
    )
    return payload


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

    from truenex_memory.ingestion.reactive_refresh import ensure_global_fresh

    _home = Path(home) if home else Path.home()
    catalog_path = Path(catalog) if catalog else _home / ".truenex-memory" / "sources.json"
    db_path = Path(db) if db else _home / ".truenex-memory" / "truenex_memory.db"

    report = build_project_context(
        project_query=project,
        catalog_path=catalog_path,
        db_path=db_path,
        limit=limit,
    )
    payload = report.to_dict()
    if report.resolved:
        # L'eta' che conta qui e' quella delle sorgenti DI QUESTO progetto, non
        # del corpus intero: un progetto silenzioso mentre altri 59 vengono
        # aggiornati avrebbe last_indexed_at globale fresco e contesto vecchio
        # lo stesso.
        project_last_indexed = max(
            (entry.get("last_indexed_at") for entry in report.ledger_entries if entry.get("last_indexed_at")),
            default=None,
        )
        payload["reactive_refresh"] = ensure_global_fresh(
            catalog_path=catalog_path,
            db_path=db_path,
            last_indexed_at=project_last_indexed,
        )
    return payload


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

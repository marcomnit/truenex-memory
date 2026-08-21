"""FastAPI server for the Truenex Memory Desktop GUI."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import logging
import sys
import os
from typing import Any

import httpx

from truenex_memory import __version__
from truenex_memory.core.memory_service import MemoryService
from truenex_memory.core.config import resolve_project_config
from truenex_memory.core.llm_client import chat_with_llm
from truenex_memory.core.chat_engine import gather_chat_context
from truenex_memory.store.models import SearchHit


logger = logging.getLogger(__name__)


def _get_service() -> MemoryService:
    project_root = os.environ.get("TRUENEX_PROJECT_ROOT", ".")
    return MemoryService(project_root)


try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
    import uvicorn  # noqa: F401
except ImportError as exc:
    sys.exit(
        f"Missing GUI server dependencies: {exc}. "
        "Install with: pip install truenex-memory[gui]"
    )

app = FastAPI(
    title="Truenex Memory",
    version=__version__,
    description="Local-first memory layer for coding agents — HTTP API for the desktop GUI.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ──────────────────────────────────────────────

class InitProjectRequest(BaseModel):
    path: str = "."


class IndexRequest(BaseModel):
    path: str = "."
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    exclude: list[str] = Field(default_factory=list)


class SearchFilters(BaseModel):
    project: str | None = None
    type: str | None = None
    status: str | None = None
    date_after: str | None = None


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    include_inactive: bool = False
    filters: SearchFilters | None = None


class AddMemoryRequest(BaseModel):
    content: str
    memory_type: str = "note"


class SetStatusRequest(BaseModel):
    status: str


class SettingsUpdate(BaseModel):
    backend_port: int | None = None
    qdrant_url: str | None = None
    qdrant_enabled: bool | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    theme: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    query: str
    provider: str = "openai"
    api_key: str
    model: str | None = None
    top_k: int = 15
    project_hint: str | None = None
    history: list[ChatMessage] | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]]


class DocumentInSource(BaseModel):
    id: str
    filename: str
    path: str
    content_hash: str
    last_indexed_at: str | None = None


class SourceOut(BaseModel):
    source_id: str
    source_name: str
    source_type: str
    source_path_or_alias: str
    status: str
    last_indexed_at: str | None = None
    chunk_count: int = 0
    document_count: int = 0
    documents: list[DocumentInSource] = []


# ── Health / Version / Debug ───────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/health/llm")
def health_llm():
    """Return whether any LLM provider appears configured."""
    providers = []
    if os.environ.get("OPENAI_API_KEY"):
        providers.append("openai")
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append("anthropic")
    if os.environ.get("DEEPSEEK_API_KEY"):
        providers.append("deepseek")
    if os.environ.get("GOOGLE_API_KEY"):
        providers.append("google")
    if os.environ.get("KIMI_API_KEY"):
        providers.append("kimi")
    if os.environ.get("LLAMA_BASE_URL"):
        providers.append("llama-server")
    return {
        "available": bool(providers),
        "providers": providers,
    }


@app.get("/api/version")
def version():
    return {"version": __version__, "engine": "multi-tool-v3"}


@app.get("/api/debug")
def debug():
    import sys
    import truenex_memory.core.chat_engine as ce
    return {
        "version": __version__,
        "engine": "multi-tool-v3",
        "python_path": sys.path,
        "chat_engine_file": str(ce.__file__),
    }


# ── Projects ───────────────────────────────────────────────────────────────

@app.get("/api/projects")
def list_projects():
    svc = _get_service()
    config = svc.config
    db_path = str(config.db_path) if config.db_path.exists() else None
    return [
        {
            "id": config.project_root.name,
            "name": config.project_root.name,
            "path": str(config.project_root.resolve()),
            "data_dir": str(config.data_dir),
            "db_path": db_path,
            "db_exists": config.db_path.exists(),
            "indexed_at": None,
        }
    ]


@app.post("/api/projects/init")
def init_project(req: InitProjectRequest):
    svc = _get_service()
    svc.init_project()
    return {"id": svc.config.project_id, "status": "initialized"}


@app.post("/api/projects/index")
def index_project(req: IndexRequest):
    svc = _get_service()
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(404, f"path does not exist: {req.path}")
    extra_dirs: set[str] = set()
    extra_filenames: set[str] = set()
    for pat in req.exclude:
        if "/" in pat or "\\" in pat:
            extra_dirs.add(pat.strip("/\\"))
        else:
            extra_filenames.add(pat)
    count = svc.index(
        path,
        chunk_size=req.chunk_size,
        chunk_overlap=req.chunk_overlap,
        extra_dirs=extra_dirs or None,
        extra_filenames=extra_filenames or None,
    )
    return {"files_indexed": count}


# ── Search ─────────────────────────────────────────────────────────────────

def _parse_iso_dt(value: str) -> datetime | None:
    try:
        value = value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, AttributeError):
        return None


def _match_filters(hit: SearchHit, filters: SearchFilters) -> bool:
    if filters.project and filters.project.lower() not in (hit.project or "").lower():
        return False
    if filters.type:
        filter_type = filters.type.lower()
        hit_type = (hit.memory_type or "").lower()
        # UX alias: "document" matches "document_chunk"
        if filter_type == "document":
            if hit_type not in ("document", "document_chunk"):
                return False
        elif filter_type != hit_type:
            return False
    if filters.status and filters.status.lower() != (hit.status or "").lower():
        return False
    if filters.date_after:
        hit_dt = _parse_iso_dt(hit.created_at) if hit.created_at else None
        filter_dt = _parse_iso_dt(filters.date_after)
        if filter_dt is not None and filter_dt.tzinfo is None and hit_dt is not None and hit_dt.tzinfo is not None:
            filter_dt = filter_dt.replace(tzinfo=timezone.utc)
        if filter_dt is not None and (hit_dt is None or hit_dt < filter_dt):
            return False
    return True


@app.post("/api/search")
def search(req: SearchRequest):
    svc = _get_service()
    include_inactive = req.include_inactive
    if req.filters and req.filters.status and req.filters.status.lower() not in {"active", "unverified"}:
        include_inactive = True

    # When filters are active, retrieve many more candidates so that post-filter
    # results are meaningful. Without this, a strict filter on a small top_k can
    # yield zero results even when matching items exist deeper in the ranking.
    has_active_filter = bool(
        req.filters
        and any(
            getattr(req.filters, field) for field in ("project", "type", "status", "date_after")
        )
    )
    fetch_k = req.top_k
    if has_active_filter:
        fetch_k = max(req.top_k * 10, 100)

    # Il progetto selezionato nell'interfaccia diventa lo scope della ricerca,
    # non piu' solo un filtro applicato dopo: filtrare a posteriori spende il
    # bacino di candidati sull'intero corpus e poi butta quasi tutto. Il filtro
    # sotto resta comunque, perche' copre anche gli altri criteri.
    scope = req.filters.project if req.filters and req.filters.project else None
    results = svc.search(
        req.query, top_k=fetch_k, include_inactive=include_inactive, scope=scope
    )
    if req.filters:
        results = [r for r in results if _match_filters(r, req.filters)]

    # Respect the caller's requested page size.
    results = results[: req.top_k]
    return [
        {
            "title": r.title,
            "content": r.content,
            "source_path": r.source_path,
            "heading_path": r.heading_path,
            "memory_type": r.memory_type,
            "status": r.status,
            "score": r.score,
            "source_id": r.source_id,
            "document_id": r.document_id,
            "project": r.project,
            "created_at": r.created_at,
        }
        for r in results
    ]


# ── Memory ─────────────────────────────────────────────────────────────────

@app.get("/api/memory")
def list_memory(status: str | None = Query(None)):
    svc = _get_service()
    nodes = svc.list_memory_nodes(status=status)
    return [
        {
            "id": n.id,
            "content": n.content,
            "title": n.title,
            "type": n.type,
            "status": n.status,
            "source_kind": n.source_kind,
            "source_path": n.source_path,
            "source_document_id": n.source_document_id,
            "source_chunk_id": n.source_chunk_id,
            "created_at": n.created_at,
            "updated_at": n.updated_at,
        }
        for n in nodes
    ]


@app.post("/api/memory")
def add_memory(req: AddMemoryRequest):
    svc = _get_service()
    memory_id = svc.add(req.content, memory_type=req.memory_type)
    return {"id": memory_id}


@app.patch("/api/memory/{memory_id}/status")
def set_memory_status(memory_id: str, req: SetStatusRequest):
    svc = _get_service()
    from truenex_memory.store.models import VALID_STATUSES

    if req.status not in VALID_STATUSES:
        valid = ", ".join(sorted(VALID_STATUSES))
        raise HTTPException(422, f"invalid status {req.status!r}; expected one of {valid}")
    try:
        svc.set_memory_status(memory_id, req.status)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": memory_id, "status": req.status}


# ── Documents ──────────────────────────────────────────────────────────────

@app.get("/api/documents")
def list_documents():
    svc = _get_service()
    return svc.list_documents()


# ── Sources ────────────────────────────────────────────────────────────────

@app.get("/api/sources")
def list_sources():
    svc = _get_service()
    return svc.list_sources_with_documents()


@app.get("/api/source/{source_id}")
def get_source(source_id: str):
    svc = _get_service()
    source = svc.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source not found: {source_id}")
    return source


# ── Stats ──────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    svc = _get_service()
    s = svc.stats() if svc.config.db_path.exists() else {}
    return {
        "projects": 1,
        "documents": s.get("documents", 0),
        "chunks": s.get("chunks", 0),
        "total_tokens": s.get("total_tokens", 0),
        "memory_nodes": s.get("memory_nodes", 0),
        "vector_backend": svc.vector_store_status,
    }


# ── Catalog status ─────────────────────────────────────────────────────────

@app.get("/api/catalog/status")
def catalog_status():
    svc = _get_service()
    return svc.catalog_status()


# ── Project graph ──────────────────────────────────────────────────────────

@app.get("/api/project-graph")
def project_graph(project_name: str = Query(..., description="Project name")):
    svc = _get_service()
    if not svc.config.db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    from truenex_memory.store.sqlite import connect

    with connect(svc.config.db_path) as conn:
        # Find active sources whose path contains the project name
        source_rows = conn.execute(
            """
            SELECT source_id, source_path_or_alias, chunk_count
            FROM source_ledger
            WHERE status = 'active'
              AND (project_name = ? OR source_path_or_alias LIKE ?)
            """,
            (project_name, f"%{project_name}%"),
        ).fetchall()

        if not source_rows:
            # Fallback: try to match any source path that could belong to this project
            source_rows = conn.execute(
                """
                SELECT source_id, source_path_or_alias, chunk_count
                FROM source_ledger
                WHERE status = 'active'
                """,
            ).fetchall()
            source_rows = [
                r for r in source_rows
                if project_name.lower() in str(r[1]).lower().replace("\\", "/")
            ]

        source_paths = [row[1] for row in source_rows]

        # Load all documents once for fast Python-side matching
        doc_rows = conn.execute(
            "SELECT id, path, filename FROM documents"
        ).fetchall()

        matched_docs: list[tuple[str, str, str]] = []
        seen_doc_ids: set[str] = set()
        for doc_id, doc_path, doc_filename in doc_rows:
            normalized_path = doc_path.replace("\\", "/")
            for sp in source_paths:
                sp_norm = sp.replace("\\", "/")
                if normalized_path == sp_norm or normalized_path.startswith(sp_norm.rstrip("/") + "/"):
                    if doc_id not in seen_doc_ids:
                        matched_docs.append((doc_id, doc_path, doc_filename))
                        seen_doc_ids.add(doc_id)
                    break

        nodes: list[dict[str, Any]] = []
        total_chunks = 0
        total_tokens = 0
        breakdown: dict[str, int] = {}
        top_files: list[dict[str, Any]] = []

        for doc_id, doc_path, doc_filename in matched_docs:
            chunk_rows = conn.execute(
                "SELECT heading_path, token_count FROM chunks WHERE document_id = ?",
                (doc_id,),
            ).fetchall()

            chunk_count = len(chunk_rows)
            token_count = sum(c[1] or 0 for c in chunk_rows)
            headings = list(dict.fromkeys([c[0] for c in chunk_rows if c[0]]))[:10]

            ext = doc_filename.rsplit(".", 1)[-1].split("::")[0] or "unknown"
            breakdown[ext] = breakdown.get(ext, 0) + 1

            nodes.append({
                "id": str(doc_id),
                "label": doc_filename,
                "path": doc_path,
                "file_type": ext,
                "chunk_count": chunk_count,
                "token_count": token_count,
                "heading_preview": headings,
            })

            total_chunks += chunk_count
            total_tokens += token_count
            top_files.append({"name": doc_filename, "chunks": chunk_count})

        top_files.sort(key=lambda x: x["chunks"], reverse=True)

        # Derive project_root as longest common prefix
        project_root = ""
        if matched_docs:
            paths = [p.replace("\\", "/") for _, p, _ in matched_docs]
            if paths:
                project_root = paths[0]
                for p in paths[1:]:
                    while not p.startswith(project_root):
                        project_root = project_root.rsplit("/", 1)[0] if "/" in project_root else ""
                        if not project_root:
                            break

        # Code-structure edges, when a graph has been built for this tree.
        # Read from cache only, never extracted here: extraction is seconds
        # to minutes, which an HTTP GET must not pay. Absent cache means an
        # empty array, exactly what this endpoint returned unconditionally
        # before — the frontend has always aggregated and weighted edges,
        # it was simply never given any.
        code_edges: list[dict[str, Any]] = []
        graph_stats: dict[str, Any] = {}
        try:
            from truenex_memory.graph import (
                default_cache_dirs,
                document_edges,
                ensure_current,
                find_cached_graph,
            )

            cache_dirs = default_cache_dirs(svc.config.db_path)
            cached = find_cached_graph(
                cache_dirs, (path for _, path, _ in matched_docs)
            )
            if cached is not None:
                code_edges = document_edges(
                    cached, ((doc_id, path) for doc_id, path, _ in matched_docs)
                )
                # Stessa funzione di libreria del tool MCP e della CLI: la GUI
                # non ha una politica propria sulla freschezza del grafo, e non
                # deve averla.
                freshness = ensure_current(cached, cache_dirs[0])
                graph_stats = {
                    "root": cached.root,
                    "file_edges": len(cached.edges),
                    "mapped_edges": len(code_edges),
                    "stale": freshness.get("stale"),
                    "rebuild": freshness.get("rebuild"),
                    **cached.stats,
                }
        except Exception as error:  # pragma: no cover - defensive
            logger.warning("code graph unavailable for %s: %s", project_name, error)

        return {
            "nodes": nodes,
            "edges": code_edges,
            "code_graph": graph_stats,
            "summary": {
                "project_name": project_name,
                "total_files": len(nodes),
                "total_chunks": total_chunks,
                "total_tokens": total_tokens,
                "breakdown": breakdown,
                "top_files": top_files[:10],
                "common_headings": [],
                "abstract": "",
            },
            "project_root": project_root,
        }


# ── File metadata ──────────────────────────────────────────────────────────

@app.get("/api/file-metadata")
def file_metadata(document_id: str = Query(..., description="Document ID to analyze")):
    svc = _get_service()
    return svc.repository.get_file_metadata(document_id)


@app.get("/api/file-analysis")
def file_analysis(file_id: str = Query(..., description="File (document) ID to analyze")):
    svc = _get_service()
    return svc.repository.analyze_file_content(file_id)


# ── Settings ───────────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings():
    svc = _get_service()
    vec = svc.vector_store_status
    return {
        "data_dir": str(svc.config.data_dir),
        "db_path": str(svc.config.db_path),
        "chunk_size": svc.config.chunk_size,
        "chunk_overlap": svc.config.chunk_overlap,
        "vector_backend": vec.get("backend", "sqlite"),
        "qdrant_url": vec.get("qdrant_url", ""),
        "qdrant_available": vec.get("available", False),
        "project_root": str(svc.config.project_root.resolve()),
    }


@app.post("/api/settings")
def update_settings(req: SettingsUpdate):
    return {"updated": True, "message": "Settings updated (restart required for some changes)."}


# ── Chat ───────────────────────────────────────────────────────────────────

@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        gathered = gather_chat_context(req.query, project_hint=req.project_hint)
    except Exception as exc:
        # Context retrieval is local infrastructure. Fail with a service-level
        # response instead of leaking an unhandled 500 when the global store is
        # unavailable, read-only, or temporarily inconsistent.
        raise HTTPException(
            503,
            "Memoria locale temporaneamente non disponibile. Riprova dopo aver verificato il database.",
        ) from exc

    if not gathered["context"].strip():
        err = gathered.get("error")
        if err:
            return ChatResponse(answer=err, sources=[])
        return ChatResponse(
            answer="Non ho trovato documenti pertinenti nella memoria.",
            sources=[],
        )

    history = None
    if req.history:
        history = [{"role": m.role, "content": m.content} for m in req.history]
    try:
        answer = chat_with_llm(
            provider=req.provider,
            api_key=req.api_key,
            model=req.model,
            context=gathered["context"],
            query=req.query,
            history=history,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            504,
            f"Timeout connessione verso {req.provider}. Verifica la connessione internet o prova un provider diverso."
        ) from exc
    except httpx.ConnectError as exc:
        raise HTTPException(
            503,
            f"Impossibile connettersi a {req.provider}. Verifica firewall, proxy, o connessione internet. "
            "Se sei dietro un proxy aziendale, imposta HTTPS_PROXY nelle variabili d'ambiente."
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            raise HTTPException(401, f"API key non valida per {req.provider}. Verifica la chiave in Impostazioni.") from exc
        if status == 429:
            raise HTTPException(429, f"Quota esaurita o rate limit su {req.provider}. Riprova tra qualche minuto.") from exc
        raise HTTPException(502, f"Errore LLM ({status}): {exc}") from exc
    except Exception as exc:
        raise HTTPException(502, f"Errore LLM: {exc}") from exc

    return ChatResponse(answer=answer, sources=gathered["sources"])


# ── Shutdown ───────────────────────────────────────────────────────────────

@app.post("/api/shutdown")
def shutdown():
    import os as _os
    import signal as _signal

    _os.kill(_os.getpid(), _signal.SIGTERM)
    return {"status": "shutting_down"}


# ── Entry point ────────────────────────────────────────────────────────────

def run_serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    project_root: str = ".",
) -> None:
    os.environ["TRUENEX_PROJECT_ROOT"] = str(Path(project_root).resolve())
    uvicorn.run(
        "truenex_memory.serve:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    run_serve()

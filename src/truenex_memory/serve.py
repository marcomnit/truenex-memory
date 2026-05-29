"""FastAPI server for the Truenex Memory Desktop GUI."""

from __future__ import annotations

from pathlib import Path
import sys
import os
from typing import Any

import httpx

from truenex_memory import __version__
from truenex_memory.core.memory_service import MemoryService
from truenex_memory.core.config import resolve_project_config
from truenex_memory.core.llm_client import chat_with_llm
from truenex_memory.core.chat_engine import gather_chat_context


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


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    include_inactive: bool = False


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
            "id": config.project_id,
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

@app.post("/api/search")
def search(req: SearchRequest):
    svc = _get_service()
    results = svc.search(req.query, top_k=req.top_k, include_inactive=req.include_inactive)
    return [
        {
            "title": r.title,
            "content": r.content,
            "source_path": r.source_path,
            "heading_path": r.heading_path,
            "memory_type": r.memory_type,
            "status": r.status,
            "score": r.score,
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
    gathered = gather_chat_context(req.query, project_hint=req.project_hint)

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

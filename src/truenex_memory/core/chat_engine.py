"""Multi-tool retrieval engine for chat: filesystem + global catalog + semantic DB."""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import Any

from truenex_memory.core.config import DATA_DIR_NAME, DB_FILENAME
from truenex_memory.ingestion.global_context import build_project_context, format_context_report


# ── Paths ─────────────────────────────────────────────────────────────────


def _get_global_paths() -> tuple[Path, Path]:
    home = Path.home()
    data_dir = home / DATA_DIR_NAME
    db_path = data_dir / DB_FILENAME
    catalog_path = data_dir / "sources.json"
    return catalog_path, db_path


# ── Filesystem tool ───────────────────────────────────────────────────────


def _read_foundation_docs(
    project_root: Path,
    query: str | None = None,
) -> list[tuple[str, str]]:
    """Read canonical markdown docs from a project root.

    Returns list of (relative_path, content).
    Prioritises AGENTS.md and README.md, then selects at most 5 docs/*.md
    files that appear most relevant to the query.
    """
    docs: list[tuple[str, str]] = []
    if not project_root.exists():
        return docs

    candidates: list[Path] = [
        project_root / "AGENTS.md",
        project_root / "README.md",
        project_root / "README",
    ]

    docs_dir = project_root / "docs"
    if docs_dir.is_dir():
        md_files = [
            f for f in docs_dir.glob("*.md")
            if "semantic-rag-architecture-plan" not in f.name.lower()
        ]
        # If query provided, score docs by keyword overlap and pick top 5
        if query and md_files:
            query_words = set(query.lower().split())
            scored = []
            for md_file in md_files:
                try:
                    text = md_file.read_text(encoding="utf-8", errors="replace").lower()
                    score = sum(1 for w in query_words if w in text)
                    scored.append((score, md_file))
                except Exception:
                    continue
            scored.sort(key=lambda x: x[0], reverse=True)
            md_files = [p for _, p in scored[:5]]
        else:
            md_files = sorted(md_files)[:5]
        candidates.extend(md_files)

    for path in candidates:
        if path.exists() and path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                rel = str(path.relative_to(project_root)).replace("\\", "/")
                docs.append((rel, content))
            except Exception:
                continue
    return docs


# ── Catalog inference ─────────────────────────────────────────────────────


def _infer_project_hint(query: str, catalog_path: Path) -> str | None:
    """Cheap heuristic: if the query contains a confirmed project name, return it."""
    if not catalog_path.exists():
        return None

    try:
        catalog_data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    entries = catalog_data.get("entries", [])
    confirmed_names: list[str] = []
    for e in entries:
        if e.get("confirmation_status") != "confirmed":
            continue
        if e.get("source_type") != "project_root":
            continue
        name = e.get("project_name") or e.get("path_or_alias", "").replace("\\", "/").rstrip("/").split("/")[-1]
        if name:
            confirmed_names.append(name)

    query_lower = query.lower()

    # Phase 1: exact substring (prefer longest / most specific)
    matches = [n for n in confirmed_names if n.lower() in query_lower]
    if matches:
        return max(matches, key=len)

    # Phase 2: fuzzy match on query words against project names and segments
    query_words = [w.strip(".,;:!?") for w in query_lower.split() if len(w.strip(".,;:!?")) >= 4]
    for word in query_words:
        for name in confirmed_names:
            candidates = {name.lower()}
            # basename
            basename = name.lower().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            candidates.add(basename)
            # segments (e.g. truenex-memory -> truenex, memory)
            for seg in basename.replace("_", "-").split("-"):
                if len(seg) >= 4:
                    candidates.add(seg)
            for cand in candidates:
                ratio = difflib.SequenceMatcher(None, word, cand).ratio()
                if ratio >= 0.80:
                    return name
    return None


# ── Semantic search tools ─────────────────────────────────────────────────


def _search_global_db(query: str, top_k: int = 15) -> list[dict[str, Any]]:
    """Semantic search against the global SQLite DB (unfiltered)."""
    from truenex_memory.store.repository import MemoryRepository
    from truenex_memory.core.embedder import embedder_from_env

    _, db_path = _get_global_paths()
    if not db_path.exists():
        return []

    embedder = embedder_from_env()
    repo = MemoryRepository(db_path, embedder=embedder)
    hits = repo.search(query, top_k=top_k)
    return [
        {
            "title": h.title,
            "content": h.content,
            "source_path": h.source_path,
            "heading_path": h.heading_path,
            "memory_type": h.memory_type,
            "status": h.status,
            "score": h.score,
        }
        for h in hits
    ]


def _search_project_chunks(
    query: str,
    project_root: Path,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Semantic search restricted to a project path.

    Uses MemoryRepository semantic search if a real embedder is available,
    otherwise falls back to BM25.
    """
    from truenex_memory.store.repository import MemoryRepository
    from truenex_memory.core.embedder import embedder_from_env

    _, db_path = _get_global_paths()
    if not db_path.exists():
        return []

    # Embedder selected via the TRUENEX_EMBEDDER env var (hashing default).
    embedder = embedder_from_env()

    repo = MemoryRepository(db_path, embedder=embedder)
    # Lo scope va DENTRO la ricerca, non applicato dopo: chiedere 50 candidati
    # sull'intero corpus e poi tenere quelli del progetto spende il bacino sul
    # resto dello store. Misurato sullo store reale: la ricerca ristretta
    # triplica le risposte trovate su domande formulate senza le parole del
    # documento (2/32 -> 6/32 su un insieme cieco). Il prefisso resta come
    # controllo, perche' lo scope e' una sottostringa e puo' catturare un
    # progetto fratello con nome piu' lungo.
    prefix = str(project_root).replace("\\", "/")
    hits = repo.search(query, top_k=50, scope=prefix)

    filtered: list[dict[str, Any]] = []
    for h in hits:
        path = str(h.source_path or "").replace("\\", "/")
        if path.startswith(prefix):
            filtered.append(
                {
                    "title": h.heading_path or h.title,
                    "content": h.content,
                    "source_path": h.source_path,
                    "heading_path": h.heading_path,
                    "memory_type": h.memory_type,
                    "status": h.status,
                    "score": h.score,
                }
            )

    # Deduplicate by source_path, keep best score per document
    seen: dict[str, dict[str, Any]] = {}
    for m in filtered:
        path = m["source_path"]
        if path not in seen or seen[path]["score"] < m["score"]:
            seen[path] = m

    results = list(seen.values())
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


# ── Orchestrator ──────────────────────────────────────────────────────────


def gather_chat_context(
    query: str,
    project_hint: str | None = None,
) -> dict[str, Any]:
    """Gather structured context from multiple sources.

    Returns dict with keys:
        - context: str (assembled for the LLM prompt)
        - sources: list[dict] (search hits for citation)
        - project_resolved: bool
        - project_root: str | None
    """
    catalog_path, db_path = _get_global_paths()

    # 1. Resolve project hint (explicit or inferred)
    effective_hint = project_hint
    if catalog_path.exists():
        if effective_hint:
            # Fuzzy-correct explicit hint (handles typos like "treunex-memory")
            corrected = _infer_project_hint(effective_hint, catalog_path)
            if corrected:
                effective_hint = corrected
        elif not effective_hint:
            effective_hint = _infer_project_hint(query, catalog_path)

    project_context_text = ""
    resolved_root: Path | None = None
    foundation_texts: list[str] = []
    report = None

    if effective_hint:
        report = build_project_context(
            effective_hint,
            catalog_path,
            db_path,
            limit=500,
        )
        if report.resolved and report.catalog_roots:
            root = report.catalog_roots[0]
            root_path = root.get("path_or_alias", "")
            pn = root.get("project_name", "")
            parts_ctx = [f"Project: {pn or root_path}"]
            parts_ctx.append(f"Path: {root_path}")
            parts_ctx.append(f"Indexed documents: {len(report.indexed_documents)}")
            parts_ctx.append(f"Indexed chunks: {len(report.indexed_chunks)}")
            project_context_text = "\n".join(parts_ctx)
            if root_path:
                resolved_root = Path(root_path)

    # 2. Read foundation docs from filesystem
    if resolved_root and resolved_root.exists():
        docs = _read_foundation_docs(resolved_root, query=query)
        for rel_path, content in docs:
            # Cap each doc to avoid overflowing context; pertinent docs get 12K
            cap = 12000 if 'recursive' in rel_path.lower() or 'agent' in rel_path.lower() else 8000
            if len(content) > cap:
                content = content[:cap] + "\n\n[...document truncated...]"
            foundation_texts.append(f"--- {rel_path} ---\n{content}")

    # 3. Semantic search — project-restricted when we know the root
    if resolved_root is not None:
        search_results = _search_project_chunks(query, resolved_root, top_k=10)
        if not search_results:
            # Fallback: try global search if project-restricted search yields nothing
            search_results = _search_global_db(query, top_k=10)
    else:
        search_results = _search_global_db(query, top_k=15)

    # 4. Assemble structured context
    # Priority: foundation docs first (most reliable for overview),
    # then project context, then indexed excerpts for detail.
    parts: list[str] = []

    if foundation_texts:
        parts.append(
            "## Foundation Documents (canonical project docs)\n\n"
            + "\n\n".join(foundation_texts)
        )

    if project_context_text:
        parts.append(f"## Project Context\n\n{project_context_text}")

    if search_results:
        chunks_text = "\n\n".join(
            f"Document: {r['source_path']}\n"
            f"Section: {r['heading_path'] or r['title']}\n"
            f"{r['content']}"
            for r in search_results
        )
        parts.append(
            "## Relevant excerpts from indexed memory\n\n" + chunks_text
        )

    if not parts:
        if resolved_root is None and effective_hint:
            # Project hint was given but could not be resolved
            return {
                "context": "",
                "sources": [],
                "project_resolved": False,
                "project_root": None,
                "error": f"Progetto '{effective_hint}' non trovato nel catalogo. Verifica il nome o indicizzalo prima.",
            }
        if resolved_root is None:
            # No project could be inferred
            return {
                "context": "",
                "sources": [],
                "project_resolved": False,
                "project_root": None,
                "error": "Nessun progetto specificato. Scrivi il nome del progetto nel campo 'Progetto' sopra la chat.",
            }
        return {
            "context": "",
            "sources": [],
            "project_resolved": False,
            "project_root": None,
        }

    assembled = "\n\n".join(parts)

    # 5. Hard cap to avoid blowing small-context models
    MAX_CONTEXT_CHARS = 45_000
    if len(assembled) > MAX_CONTEXT_CHARS:
        # Truncate from the end (indexed excerpts first, then project context)
        assembled = assembled[:MAX_CONTEXT_CHARS]
        assembled += "\n\n[...context truncated due to length...]"

    return {
        "context": assembled,
        "sources": search_results,
        "project_resolved": resolved_root is not None,
        "project_root": str(resolved_root) if resolved_root else None,
    }

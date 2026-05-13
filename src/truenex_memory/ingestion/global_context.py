"""Read-only project context command for the Truenex Memory global store.

Resolves a project from the confirmed source catalog and reads the SQLite
global DB/ledger/index without mutating anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
import json


# ── Report dataclass ──────────────────────────────────────────────────

@dataclass
class ProjectContextReport:
    project_query: str
    catalog_path: str
    db_path: str

    resolved: bool = False
    resolution_method: str | None = None
    resolution_notes: str | None = None

    catalog_roots: list[dict[str, object]] = field(default_factory=list)
    catalog_documents: list[dict[str, object]] = field(default_factory=list)
    catalog_server_aliases: list[dict[str, object]] = field(default_factory=list)

    ledger_entries: list[dict[str, object]] = field(default_factory=list)
    indexed_documents: list[dict[str, object]] = field(default_factory=list)
    indexed_chunks: list[dict[str, object]] = field(default_factory=list)
    memory_nodes: list[dict[str, object]] = field(default_factory=list)

    ambiguous_candidates: list[str] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "project_query": self.project_query,
            "catalog_path": self.catalog_path,
            "db_path": self.db_path,
            "resolved": self.resolved,
            "resolution_method": self.resolution_method,
            "resolution_notes": self.resolution_notes,
            "catalog": {
                "roots": self.catalog_roots,
                "documents": self.catalog_documents,
                "server_aliases": self.catalog_server_aliases,
            },
            "ledger": self.ledger_entries,
            "indexed": {
                "documents": self.indexed_documents,
                "chunks": self.indexed_chunks,
            },
            "memory_nodes": self.memory_nodes,
            "ambiguous_candidates": self.ambiguous_candidates,
            "warnings": self.warnings,
        }


# ── Build function ────────────────────────────────────────────────────

def build_project_context(
    project_query: str,
    catalog_path: Path,
    db_path: Path,
    *,
    limit: int = 20,
) -> ProjectContextReport:
    """Build a read-only ProjectContextReport for *project_query*.

    Never creates directories, databases, catalog files, or ledger rows.
    """
    report = ProjectContextReport(
        project_query=project_query,
        catalog_path=str(catalog_path),
        db_path=str(db_path),
    )

    # 1. Check catalog exists and read it
    if not catalog_path.exists():
        report.warnings.append(f"Catalog not found: {catalog_path}")
        if not db_path.exists():
            report.warnings.append(f"Database not found: {db_path}")
        return report

    try:
        catalog_data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        report.warnings.append(f"Catalog exists but is invalid/unreadable: {catalog_path}")
        return report

    if not isinstance(catalog_data, dict):
        report.warnings.append(f"Catalog must be a JSON object: {catalog_path}")
        return report

    raw_entries = catalog_data.get("entries", [])
    if not isinstance(raw_entries, list):
        report.warnings.append("Catalog entries is not a list")
        return report

    # 2. Collect confirmed entries only
    confirmed: list[dict] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            report.warnings.append("Catalog contains non-object entries")
            continue
        if entry.get("confirmation_status") == "confirmed":
            confirmed.append(entry)

    if not confirmed:
        report.warnings.append("No confirmed entries in catalog")
        return report

    # 3. Resolve the project query against confirmed entries
    matched_roots, matched_docs, matched_servers, resolution_method, resolution_notes, ambiguous = (
        _resolve_project(project_query, confirmed)
    )

    if ambiguous:
        report.ambiguous_candidates = ambiguous
        report.warnings.append(
            f"Ambiguous project query '{project_query}' matches {len(ambiguous)} "
            f"candidates: {', '.join(ambiguous[:10])}"
        )
        # Still return what we can, but mark unresolved
        return report

    if not matched_roots:
        report.warnings.append(
            f"Project '{project_query}' not found in confirmed catalog entries"
        )
        return report

    report.resolved = True
    report.resolution_method = resolution_method
    report.resolution_notes = resolution_notes
    report.catalog_roots = matched_roots
    report.catalog_documents = matched_docs
    report.catalog_server_aliases = matched_servers

    # 4. Read ledger and indexed data from DB (if it exists)
    if db_path.exists():
        try:
            conn = _connect_readonly(db_path)
        except Exception:
            report.warnings.append(f"Database exists but cannot be opened: {db_path}")
        else:
            try:
                _read_ledger_for_project(conn, report, matched_roots)
                _read_indexed_for_project(conn, report, matched_roots, limit)
                _read_memory_nodes_for_project(conn, report, limit)
            except sqlite3.DatabaseError:
                report.warnings.append(f"Database exists but cannot be read: {db_path}")
            finally:
                conn.close()
    else:
        report.warnings.append(f"Database not found: {db_path}")

    return report


# ── Internal: resolution ──────────────────────────────────────────────

def _resolve_project(
    query: str,
    confirmed_entries: list[dict],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    str | None,
    str | None,
    list[str],
]:
    """Resolve *query* against confirmed catalog entries.

    Returns (roots, docs, servers, method, notes, ambiguous_candidates).
    """
    query_lower = query.strip().lower()

    # Separate entries by source_type
    roots = [e for e in confirmed_entries if e.get("source_type") == "project_root"]
    docs = [e for e in confirmed_entries if e.get("source_type") == "document"]
    servers = [e for e in confirmed_entries if e.get("source_type") == "server_alias"]

    # Phase 1: exact case-insensitive project_name match
    name_matches = [
        r for r in roots
        if r.get("project_name") and str(r["project_name"]).strip().lower() == query_lower
    ]
    if len(name_matches) == 1:
        root_entry = name_matches[0]
        project_name_val = root_entry.get("project_name")
        related_docs = _find_related_docs(root_entry, docs)
        related_servers = _find_related_servers(root_entry, servers)
        return (
            _serialize_entries(name_matches),
            _serialize_entries(related_docs),
            _serialize_entries(related_servers),
            "exact_name",
            f"matched project_name='{project_name_val}'",
            [],
        )
    if len(name_matches) > 1:
        candidates = [str(r.get("project_name", r.get("path_or_alias", "?"))) for r in name_matches]
        return [], [], [], None, None, candidates

    # Phase 2a: exact full path/alias match
    path_matches = []
    query_path = _normalize_path_for_match(query)
    for r in roots:
        path_or_alias = str(r.get("path_or_alias", ""))
        normalized_path = _normalize_path_for_match(path_or_alias)
        if normalized_path and normalized_path == query_path:
            path_matches.append(r)

    if len(path_matches) == 1:
        root_entry = path_matches[0]
        related_docs = _find_related_docs(root_entry, docs)
        related_servers = _find_related_servers(root_entry, servers)
        return (
            _serialize_entries(path_matches),
            _serialize_entries(related_docs),
            _serialize_entries(related_servers),
            "path_alias",
            f"matched path_or_alias='{root_entry.get('path_or_alias', '')}'",
            [],
        )
    if len(path_matches) > 1:
        candidates = [str(r.get("path_or_alias", "?")) for r in path_matches]
        return [], [], [], None, None, candidates

    # Phase 2b: exact basename match
    basename_matches = []
    for r in roots:
        path_or_alias = str(r.get("path_or_alias", ""))
        basename = _normalize_basename(path_or_alias)
        if basename and basename.lower() == query_lower:
            basename_matches.append(r)

    if len(basename_matches) == 1:
        root_entry = basename_matches[0]
        related_docs = _find_related_docs(root_entry, docs)
        related_servers = _find_related_servers(root_entry, servers)
        return (
            _serialize_entries(basename_matches),
            _serialize_entries(related_docs),
            _serialize_entries(related_servers),
            "basename",
            f"matched path basename='{_normalize_basename(str(root_entry.get('path_or_alias', '')))}'",
            [],
        )
    if len(basename_matches) > 1:
        candidates = [str(r.get("path_or_alias", "?")) for r in basename_matches]
        return [], [], [], None, None, candidates

    # Phase 3: substring fallback on project_name or path_or_alias
    substring_matches = []
    for r in roots:
        project_name = str(r.get("project_name", "")).strip().lower()
        path_or_alias = str(r.get("path_or_alias", "")).strip().lower()
        if query_lower in project_name or query_lower in path_or_alias:
            substring_matches.append(r)

    if len(substring_matches) == 1:
        root_entry = substring_matches[0]
        related_docs = _find_related_docs(root_entry, docs)
        related_servers = _find_related_servers(root_entry, servers)
        return (
            _serialize_entries(substring_matches),
            _serialize_entries(related_docs),
            _serialize_entries(related_servers),
            "substring",
            f"substring match on '{query}'",
            [],
        )
    if len(substring_matches) > 1:
        candidates = [str(r.get("path_or_alias", "?")) for r in substring_matches]
        return [], [], [], None, None, candidates

    # No match at all
    return [], [], [], None, None, []


def _normalize_basename(path_or_alias: str) -> str:
    """Extract the basename from a path or alias."""
    cleaned = path_or_alias.strip().replace("\\", "/").rstrip("/")
    if not cleaned:
        return ""
    return cleaned.rsplit("/", 1)[-1]


def _normalize_path_for_match(path_or_alias: str) -> str:
    return path_or_alias.strip().replace("\\", "/").rstrip("/").lower()


def _path_equal_or_inside(child: str, parent: str) -> bool:
    child_norm = _normalize_path_for_match(child)
    parent_norm = _normalize_path_for_match(parent)
    if not child_norm or not parent_norm:
        return False
    return child_norm == parent_norm or child_norm.startswith(parent_norm + "/")


def _find_related_docs(root_entry: dict, docs: list[dict]) -> list[dict]:
    """Find document entries related to a project root by path prefix or discovered_from."""
    root_path = str(root_entry.get("path_or_alias", ""))
    root_id = str(root_entry.get("id", ""))
    related: list[dict] = []
    for doc in docs:
        doc_path = str(doc.get("path_or_alias", ""))
        discovered = doc.get("discovered_from", [])
        if _path_equal_or_inside(doc_path, root_path):
            related.append(doc)
        elif root_id in (str(d).strip() for d in discovered):
            related.append(doc)
    return related


def _find_related_servers(root_entry: dict, servers: list[dict]) -> list[dict]:
    """Find server_alias entries related to a project root by project_name or discovered_from."""
    project_name = str(root_entry.get("project_name", "")).strip().lower()
    root_path = str(root_entry.get("path_or_alias", "")).lower()
    related: list[dict] = []
    for srv in servers:
        discovered = srv.get("discovered_from", [])
        discovered_str = " ".join(str(d) for d in discovered).lower()
        if project_name and project_name in discovered_str:
            related.append(srv)
        elif root_path and root_path in discovered_str:
            related.append(srv)
    return related


def _serialize_entries(entries: list[dict]) -> list[dict[str, object]]:
    """Serialize catalog entries to stable dicts with citation fields."""
    result: list[dict[str, object]] = []
    for e in entries:
        result.append({
            "id": e.get("id", ""),
            "source_type": e.get("source_type", ""),
            "path_or_alias": e.get("path_or_alias", ""),
            "project_name": e.get("project_name"),
            "discovered_from": e.get("discovered_from", []),
            "confirmation_status": e.get("confirmation_status", ""),
        })
    return result


# ── Internal: DB readers ──────────────────────────────────────────────

def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection. Does NOT create the file or
    parent directories."""
    uri_path = db_path.resolve().as_posix()
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _read_ledger_for_project(
    conn: sqlite3.Connection,
    report: ProjectContextReport,
    matched_roots: list[dict[str, object]],
) -> None:
    if not _table_exists(conn, "source_ledger"):
        report.warnings.append("source_ledger table not found in database")
        return

    # Build a set of source_ids from matched roots for direct lookup
    root_ids = {str(r["id"]) for r in matched_roots if r.get("id")}
    root_project_names = {
        str(r["project_name"]).strip().lower()
        for r in matched_roots
        if r.get("project_name")
    }
    root_paths = {
        str(r["path_or_alias"]).strip().lower()
        for r in matched_roots
        if r.get("path_or_alias")
    }

    # Query ledger entries that match:
    # a) source_id is a matched root id, OR
    # b) project_name matches, OR
    # c) source_path_or_alias starts with a matched root path
    all_rows = conn.execute(
        "SELECT * FROM source_ledger ORDER BY updated_at DESC"
    ).fetchall()

    matching: list[dict[str, object]] = []
    for row in all_rows:
        sid = str(row["source_id"] or "")
        pn = str(row["project_name"] or "").strip().lower()
        spa = str(row["source_path_or_alias"] or "").strip()

        # Direct match by source_id
        if sid in root_ids:
            matching.append(_ledger_row_to_dict(row))
            continue

        # Match by project_name (case-insensitive)
        if pn and pn in root_project_names:
            matching.append(_ledger_row_to_dict(row))
            continue

        # Match by path prefix
        for rp in root_paths:
            if _path_equal_or_inside(spa, rp):
                matching.append(_ledger_row_to_dict(row))
                break

    report.ledger_entries = matching


def _ledger_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "source_id": row["source_id"],
        "source_path_or_alias": row["source_path_or_alias"],
        "project_name": row["project_name"],
        "source_type": row["source_type"],
        "parser_version": row["parser_version"],
        "content_hash": row["content_hash"],
        "last_modified_at": row["last_modified_at"],
        "last_indexed_at": row["last_indexed_at"],
        "status": row["status"],
        "error_message": row["error_message"],
        "chunk_count": row["chunk_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _doc_ids_from_ledger(
    conn: sqlite3.Connection,
    root_project_names: set[str],
    root_ids: set[str],
) -> set[str]:
    """Return document ids whose path matches a ledger entry for the project."""
    if not _table_exists(conn, "source_ledger") or not _table_exists(conn, "documents"):
        return set()

    params: list[object] = []
    clauses: list[str] = []

    if root_project_names:
        placeholders = ",".join("?" for _ in root_project_names)
        clauses.append(f"LOWER(sl.project_name) IN ({placeholders})")
        params.extend(root_project_names)

    if root_ids:
        placeholders = ",".join("?" for _ in root_ids)
        clauses.append(f"sl.source_id IN ({placeholders})")
        params.extend(root_ids)

    if not clauses:
        return set()

    where = " OR ".join(clauses)
    query = f"""
        SELECT DISTINCT d.id
        FROM documents d
        JOIN source_ledger sl ON sl.source_path_or_alias = d.path
        WHERE sl.status = 'active'
          AND ({where})
    """
    rows = conn.execute(query, params).fetchall()
    return {str(row[0]) for row in rows}


def _read_indexed_for_project(
    conn: sqlite3.Connection,
    report: ProjectContextReport,
    matched_roots: list[dict[str, object]],
    limit: int,
) -> None:
    # Build project_id candidates from matched roots
    project_ids: set[str] = set()
    root_project_names: set[str] = set()
    root_ids: set[str] = set()
    for r in matched_roots:
        pid = str(r.get("project_name", "")).strip()
        if pid:
            project_ids.add(pid)
            root_project_names.add(pid.lower())
        rid = str(r.get("id", "")).strip()
        if rid:
            root_ids.add(rid)
        path_alias = str(r.get("path_or_alias", "")).strip().replace("\\", "/").rstrip("/")
        if path_alias:
            project_ids.add(path_alias)
        basename = _normalize_basename(path_alias) if path_alias else ""
        if basename:
            project_ids.add(basename)

    # Read documents
    if _table_exists(conn, "documents"):
        all_docs = conn.execute(
            "SELECT * FROM documents ORDER BY updated_at DESC"
        ).fetchall()

        # Collect doc ids via path-prefix / project_id match first
        seen_ids: set[str] = set()
        matching_docs: list[dict[str, object]] = []
        for row in all_docs:
            doc_id = str(row["id"] or "")
            pid = str(row["project_id"] or "")
            if pid in project_ids:
                matching_docs.append(_doc_row_to_dict(row))
                seen_ids.add(doc_id)
            else:
                doc_path = str(row["path"] or "").strip().replace("\\", "/").lower()
                for r in matched_roots:
                    rp = str(r.get("path_or_alias", "")).strip().replace("\\", "/").rstrip("/").lower()
                    if _path_equal_or_inside(doc_path, rp):
                        matching_docs.append(_doc_row_to_dict(row))
                        seen_ids.add(doc_id)
                        break

        # Ledger-based JOIN: pick up session docs not under the repo root
        ledger_doc_ids = _doc_ids_from_ledger(conn, root_project_names, root_ids)
        extra_ids = ledger_doc_ids - seen_ids
        if extra_ids:
            id_placeholders = ",".join("?" for _ in extra_ids)
            extra_rows = conn.execute(
                f"SELECT * FROM documents WHERE id IN ({id_placeholders})"
                f" ORDER BY updated_at DESC",
                list(extra_ids),
            ).fetchall()
            for row in extra_rows:
                matching_docs.append(_doc_row_to_dict(row))
                seen_ids.add(str(row["id"]))

        report.indexed_documents = matching_docs[:limit]

        # Read chunks for matching documents (respect limit)
        if _table_exists(conn, "chunks") and matching_docs:
            doc_ids = [d["id"] for d in matching_docs[:limit]]
            placeholders = ",".join("?" for _ in doc_ids)
            chunk_rows = conn.execute(
                f"SELECT * FROM chunks WHERE document_id IN ({placeholders}) "
                f"ORDER BY chunk_index LIMIT ?",
                (*doc_ids, limit),
            ).fetchall()
            report.indexed_chunks = [
                _chunk_row_to_dict(r, limit_chars=400)
                for r in chunk_rows
            ]
    else:
        report.warnings.append("documents table not found in database")


def _doc_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "path": row["path"],
        "filename": row["filename"],
        "content_hash": row["content_hash"],
        "last_indexed_at": row["last_indexed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _strip_ingestion_metadata(text: str) -> str:
    if text.startswith("TRUENEX_INGESTION_METADATA "):
        parts = text.split("\n\n", 2)
        return parts[-1].strip() if len(parts) >= 2 else ""
    return text


def _chunk_row_to_dict(row: sqlite3.Row, limit_chars: int = 400) -> dict[str, object]:
    content = _strip_ingestion_metadata(str(row["content"] or ""))
    truncated = False
    if len(content) > limit_chars:
        content = content[:limit_chars] + "..."
        truncated = True
    return {
        "id": row["id"],
        "document_id": row["document_id"],
        "chunk_index": row["chunk_index"],
        "heading_path": row["heading_path"],
        "content_excerpt": content,
        "content_hash": row["content_hash"],
        "token_count": row["token_count"],
        "truncated": truncated,
        "created_at": row["created_at"],
    }


def _read_memory_nodes_for_project(
    conn: sqlite3.Connection,
    report: ProjectContextReport,
    limit: int,
) -> None:
    if not _table_exists(conn, "memory_nodes"):
        return
    rows = conn.execute(
        """
        SELECT id, title, type AS memory_type, status, confidence, content, source_path, created_at
        FROM memory_nodes
        WHERE project_id = 'default'
          AND status IN ('active', 'unverified')
          AND (confidence IS NULL OR confidence >= 0.5)
        ORDER BY
          CASE status WHEN 'active' THEN 0 ELSE 1 END,
          confidence DESC,
          created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    report.memory_nodes = [
        {
            "id": row["id"],
            "title": row["title"],
            "memory_type": row["memory_type"],
            "status": row["status"],
            "confidence": row["confidence"],
            "content": str(row["content"] or "")[:400],
            "source_path": row["source_path"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


# ── Text formatting ───────────────────────────────────────────────────

def format_context_report(report: ProjectContextReport) -> str:
    """Format a ProjectContextReport as concise agent-usable text."""
    lines: list[str] = [f"Global Context: {report.project_query}"]
    lines.append("=" * 60)

    # Warnings first
    if report.warnings:
        for w in report.warnings:
            lines.append(f"[WARNING] {w}")
        lines.append("")

    # Ambiguous candidates
    if report.ambiguous_candidates:
        lines.append(f"Ambiguous: {len(report.ambiguous_candidates)} candidates")
        for c in report.ambiguous_candidates[:10]:
            lines.append(f"  - {c}")
        return "\n".join(lines)

    # Resolution info
    if report.resolution_method:
        lines.append(f"Resolved: {report.resolution_method} ({report.resolution_notes})")
        lines.append("")

    # Catalog roots
    if report.catalog_roots:
        lines.append("## Project Roots")
        for r in report.catalog_roots:
            pn = r.get("project_name")
            pn_str = f" [{pn}]" if pn else ""
            lines.append(f"- {r['id']} {r['path_or_alias']}{pn_str}")
        lines.append("")

    # Catalog documents
    if report.catalog_documents:
        lines.append("## Related Documents (catalog)")
        for d in report.catalog_documents:
            lines.append(f"- {d['id']} {d['path_or_alias']}")
        lines.append("")

    # Server aliases (hints only)
    if report.catalog_server_aliases:
        lines.append("## Server Aliases (hints, not executed)")
        for s in report.catalog_server_aliases:
            lines.append(f"- {s['id']} {s['path_or_alias']}")
        lines.append("")

    # Ledger
    lines.append("## Ledger")
    if not report.ledger_entries:
        lines.append("(no ledger entries found for this project)")
    else:
        by_status: dict[str, int] = {}
        for le in report.ledger_entries:
            st = str(le.get("status", "?"))
            by_status[st] = by_status.get(st, 0) + 1
        status_str = " ".join(f"{k}={v}" for k, v in sorted(by_status.items()))
        lines.append(f"{len(report.ledger_entries)} entries: {status_str}")
        for le in report.ledger_entries[:10]:
            err = le.get("error_message")
            err_str = f" ({err})" if err else ""
            lines.append(
                f"- [{le.get('status')}] {le.get('source_type')}:"
                f"{le.get('source_path_or_alias')} "
                f"chunks={le.get('chunk_count')}{err_str}"
            )
        if len(report.ledger_entries) > 10:
            lines.append(f"  ... and {len(report.ledger_entries) - 10} more")
    lines.append("")

    # Indexed
    lines.append("## Indexed")
    lines.append(f"documents: {len(report.indexed_documents)}")
    lines.append(f"chunks: {len(report.indexed_chunks)}")

    if report.indexed_documents:
        lines.append("")
        lines.append("### Documents")
        for d in report.indexed_documents[:10]:
            lines.append(f"- {d['path']} (hash={d['content_hash'][:12]}...)")

    if report.indexed_chunks:
        lines.append("")
        lines.append("### Chunks (excerpts)")
        for c in report.indexed_chunks[:5]:
            heading = c.get("heading_path") or "(no heading)"
            excerpt = str(c.get("content_excerpt", ""))[:120].replace("\n", " ")
            lines.append(f"- [{c['chunk_index']}] {heading}")
            lines.append(f"  {excerpt}")

    # Memory nodes
    if report.memory_nodes:
        lines.append("")
        lines.append("## Memory Nodes")
        for mn in report.memory_nodes:
            conf = mn.get("confidence")
            conf_str = f" confidence={conf:.2f}" if conf is not None else ""
            lines.append(
                f"- [{mn.get('status')}/{mn.get('memory_type')}]{conf_str}"
                f" {mn.get('title')}"
            )

    lines.append("")
    lines.append(f"Catalog: {report.catalog_path}")
    lines.append(f"Database: {report.db_path}")

    return "\n".join(lines)

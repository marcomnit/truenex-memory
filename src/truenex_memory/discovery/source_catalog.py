"""Source catalog domain model for confirmed local-private sources.

Discovery produces candidates.  The source catalog contains only confirmed
entries with stable deterministic ids.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import hashlib
import json

from truenex_memory.discovery.agent_discovery import (
    AgentRoot,
    CandidateDocument,
    CandidateProject,
    DiscoveryReport,
    ServerAlias,
    _split_sources,
)

# ── constants ─────────────────────────────────────────────────────────

DEFAULT_CATALOG_PATH = Path.home() / ".truenex-memory" / "sources.json"


def default_catalog_path(home: Path) -> Path:
    """Return the default source catalog path for a user home directory."""
    return home / ".truenex-memory" / "sources.json"


# ── stable id ─────────────────────────────────────────────────────────

def source_id(source_type: str, path_or_alias: str) -> str:
    """Return a deterministic stable id from source_type + normalized path/alias.

    Normalization: whitespace trimmed, backslashes → forward slashes,
    lowercased, trailing slash stripped.
    """
    normalized = path_or_alias.strip().replace("\\", "/").lower().rstrip("/")
    hexdigest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"{source_type}:{hexdigest}"


# ── catalog entry ─────────────────────────────────────────────────────

@dataclass
class CatalogEntry:
    """A confirmed source entry in the source catalog."""

    id: str
    source_type: str                     # agent_root | project_root | document | server_alias
    path_or_alias: str
    project_name: str | None = None
    discovered_from: list[str] = field(default_factory=list)
    confirmation_status: str = "confirmed"
    privacy_scope: str = "local-private"
    confidence: float = 0.0
    evidence_count: int = 0


# ── candidate conversion ──────────────────────────────────────────────

def candidate_to_entry(
    candidate: AgentRoot | CandidateProject | CandidateDocument | ServerAlias,
    *,
    confirmation_status: str = "confirmed",
) -> CatalogEntry:
    """Convert a discovery candidate to a CatalogEntry with a stable id."""
    if isinstance(candidate, AgentRoot):
        return _agent_root_to_entry(candidate, confirmation_status=confirmation_status)
    if isinstance(candidate, CandidateProject):
        return _project_to_entry(candidate, confirmation_status=confirmation_status)
    if isinstance(candidate, CandidateDocument):
        return _document_to_entry(candidate, confirmation_status=confirmation_status)
    if isinstance(candidate, ServerAlias):
        return _server_to_entry(candidate, confirmation_status=confirmation_status)
    raise TypeError(f"Unknown candidate type: {type(candidate).__name__}")


def _agent_root_to_entry(root: AgentRoot, *, confirmation_status: str) -> CatalogEntry:
    path_str = str(root.path)
    return CatalogEntry(
        id=source_id("agent_root", path_str),
        source_type="agent_root",
        path_or_alias=path_str,
        discovered_from=[root.label],
        confirmation_status=confirmation_status,
        confidence=float(root.file_count) if root.exists else 0.0,
        evidence_count=root.file_count,
    )


def _project_to_entry(proj: CandidateProject, *, confirmation_status: str) -> CatalogEntry:
    project_name = _infer_project_name(proj.root)
    return CatalogEntry(
        id=source_id("project_root", proj.root),
        source_type="project_root",
        path_or_alias=proj.root,
        project_name=project_name,
        discovered_from=list(proj.discovered_from),
        confirmation_status=confirmation_status,
        confidence=proj.confidence,
        evidence_count=len(proj.discovered_from),
    )


def _infer_project_name(path_or_alias: str) -> str | None:
    cleaned = path_or_alias.strip().replace("\\", "/").rstrip("/")
    if not cleaned:
        return None
    return cleaned.rsplit("/", 1)[-1] or None


_INDEX_DOC_NAMES: frozenset[str] = frozenset({"skill.md", "readme.md", "agents.md", "claude.md"})


def _infer_project_name_from_doc(path_str: str) -> str | None:
    """Return the parent directory name when *path_str* names a known index document.

    Known index document names (case-insensitive):
      skill.md, readme.md, agents.md, claude.md

    Returns None for all other file names, and for paths where the parent
    is empty, ``"."``, or ``".."``.
    """
    cleaned = path_str.strip().replace("\\", "/")
    if not cleaned:
        return None
    filename = cleaned.rsplit("/", 1)[-1]
    if filename.lower() not in _INDEX_DOC_NAMES:
        return None
    if "/" not in cleaned:
        return None
    parent_part = cleaned.rsplit("/", 1)[0]
    parent_name = parent_part.rsplit("/", 1)[-1] if "/" in parent_part else parent_part
    if not parent_name or parent_name in (".", ".."):
        return None
    return parent_name


def _document_to_entry(doc: CandidateDocument, *, confirmation_status: str) -> CatalogEntry:
    return CatalogEntry(
        id=source_id("document", doc.path),
        source_type="document",
        path_or_alias=doc.path,
        project_name=_infer_project_name_from_doc(doc.path),
        discovered_from=list(doc.discovered_from),
        confirmation_status=confirmation_status,
        confidence=doc.confidence,
        evidence_count=len(doc.discovered_from),
    )


def _server_to_entry(srv: ServerAlias, *, confirmation_status: str) -> CatalogEntry:
    sources = _split_sources(srv.source)
    return CatalogEntry(
        id=source_id("server_alias", srv.alias),
        source_type="server_alias",
        path_or_alias=srv.alias,
        discovered_from=sources,
        confirmation_status=confirmation_status,
        confidence=srv.confidence,
        evidence_count=len(sources),
    )


def report_to_entries(
    report: DiscoveryReport,
    limit: int | None = None,
    *,
    confirmation_status: str = "confirmed",
) -> list[CatalogEntry]:
    """Convert a DiscoveryReport to a list of CatalogEntry, respecting a
    per-section limit when provided.

    Only existing agent roots are included.  Each candidate section
    (projects, documents, servers) is limited independently so that a
    single noisy section cannot crowd out the others.
    """
    entries: list[CatalogEntry] = []

    for root in report.agent_roots:
        if root.exists:
            entries.append(candidate_to_entry(root, confirmation_status=confirmation_status))

    proj_candidates = report.projects[:limit] if limit is not None else report.projects
    for proj in proj_candidates:
        entries.append(candidate_to_entry(proj, confirmation_status=confirmation_status))

    doc_candidates = report.documents[:limit] if limit is not None else report.documents
    for doc in doc_candidates:
        entries.append(candidate_to_entry(doc, confirmation_status=confirmation_status))

    srv_candidates = report.servers[:limit] if limit is not None else report.servers
    for srv in srv_candidates:
        entries.append(candidate_to_entry(srv, confirmation_status=confirmation_status))

    return entries


# ── catalog persistence ───────────────────────────────────────────────

@dataclass
class SourceCatalog:
    """A collection of confirmed source catalog entries."""

    entries: list[CatalogEntry] = field(default_factory=list)
    version: str = "1"

    def save(self, path: Path) -> None:
        """Write the catalog to *path* as JSON, creating parent dirs as needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, object] = {
            "version": self.version,
            "entries": [asdict(entry) for entry in self.entries],
        }
        path.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> SourceCatalog:
        """Load a catalog from *path*.  Returns an empty catalog when the
        file does not exist."""
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [
            CatalogEntry(**entry)
            for entry in data.get("entries", [])
        ]
        version = str(data.get("version", "1"))
        return cls(entries=entries, version=version)

    def upsert_entry(self, entry: CatalogEntry) -> tuple[str, CatalogEntry]:
        """Add or replace an entry by stable id.

        Returns ``("added", entry)`` when the id is new, or
        ``("updated", entry)`` when an existing entry was replaced.
        All other entries are preserved unchanged.
        """
        for i, existing in enumerate(self.entries):
            if existing.id == entry.id:
                self.entries[i] = entry
                return ("updated", entry)
        self.entries.append(entry)
        return ("added", entry)


# ── formatting ────────────────────────────────────────────────────────

def format_entries(entries: list[CatalogEntry]) -> str:
    """Format a list of CatalogEntry as a human-readable markdown string."""
    lines: list[str] = ["# Source Catalog Candidates (review only, not written)"]

    by_type: dict[str, list[CatalogEntry]] = {}
    type_order = ("agent_root", "project_root", "document", "server_alias")
    for entry in entries:
        by_type.setdefault(entry.source_type, []).append(entry)

    for source_type in type_order:
        items = by_type.get(source_type, [])
        lines.append(f"\n## {source_type} ({len(items)})")
        if items:
            for item in items:
                extra = f" [{item.project_name}]" if item.project_name else ""
                lines.append(
                    f"- {item.path_or_alias}{extra} "
                    f"(conf={item.confidence:.1f}, evidence={item.evidence_count}, "
                    f"from: {', '.join(item.discovered_from)})"
                )
        else:
            lines.append("- (none)")

    total = len(entries)
    lines.append(f"\n## Summary")
    lines.append(f"- Total entries: {total}")
    return "\n".join(lines)


def entries_to_dict(entries: list[CatalogEntry]) -> dict[str, object]:
    """Serialize a list of CatalogEntry to a JSON-friendly dict."""
    return {
        "version": "1",
        "entries": [asdict(entry) for entry in entries],
    }

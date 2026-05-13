"""Agent discovery - find projects, docs, servers from local agent clients."""

from truenex_memory.discovery.agent_discovery import (
    AgentRoot,
    CandidateDocument,
    CandidateProject,
    DiscoveryReport,
    ServerAlias,
    discover_from_agents,
)
from truenex_memory.discovery.source_catalog import (
    CatalogEntry,
    SourceCatalog,
    candidate_to_entry,
    default_catalog_path,
    entries_to_dict,
    format_entries,
    report_to_entries,
    source_id,
)

__all__ = [
    "AgentRoot",
    "CandidateDocument",
    "CandidateProject",
    "CatalogEntry",
    "DiscoveryReport",
    "ServerAlias",
    "SourceCatalog",
    "candidate_to_entry",
    "default_catalog_path",
    "discover_from_agents",
    "entries_to_dict",
    "format_entries",
    "report_to_entries",
    "source_id",
]

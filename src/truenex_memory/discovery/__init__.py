"""Agent discovery - find projects, docs, servers from local agent clients."""

from truenex_memory.discovery.agent_discovery import (
    AgentRoot,
    CandidateDocument,
    CandidateProject,
    DiscoveryReport,
    ServerAlias,
    add_agent_to_manifest,
    discover_from_agents,
    load_agent_manifest,
    remove_agent_from_manifest,
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
    "add_agent_to_manifest",
    "candidate_to_entry",
    "default_catalog_path",
    "discover_from_agents",
    "entries_to_dict",
    "format_entries",
    "load_agent_manifest",
    "remove_agent_from_manifest",
    "report_to_entries",
    "source_id",
]

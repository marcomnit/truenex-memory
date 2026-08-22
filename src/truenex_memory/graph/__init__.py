"""Code-structure graph: relations between files, for the project view.

The store answers what was written and decided; this answers how the code
is actually wired. Backed by the optional `graph` extra.
"""

from truenex_memory.graph.code_graph import (
    CACHE_VERSION,
    CODE_SUFFIXES,
    DEFAULT_CODE_SUFFIXES,
    EXPLAIN_GROUP_LIMIT,
    GRAPH_EXTRA_EXCLUDED_DIRS,
    EntityEdge,
    FileEdge,
    FileGraph,
    GraphifyUnavailable,
    aggregate_to_files,
    collect_entity_edges,
    build_file_graph,
    cache_path,
    code_suffixes,
    collect_source_files,
    default_cache_dirs,
    document_edges,
    explain_entity,
    find_cached_graph,
    graphify_available,
    load_file_graph,
    save_file_graph,
    text_call_sites,
)
from truenex_memory.graph.refresh import (
    AUTO_REBUILD_ENV,
    auto_rebuild_enabled,
    ensure_current,
    release_lock,
)

__all__ = [
    "CACHE_VERSION",
    "CODE_SUFFIXES",
    "DEFAULT_CODE_SUFFIXES",
    "EXPLAIN_GROUP_LIMIT",
    "GRAPH_EXTRA_EXCLUDED_DIRS",
    "EntityEdge",
    "FileEdge",
    "FileGraph",
    "GraphifyUnavailable",
    "aggregate_to_files",
    "collect_entity_edges",
    "build_file_graph",
    "cache_path",
    "code_suffixes",
    "collect_source_files",
    "default_cache_dirs",
    "document_edges",
    "explain_entity",
    "find_cached_graph",
    "graphify_available",
    "load_file_graph",
    "save_file_graph",
    "text_call_sites",
    "AUTO_REBUILD_ENV",
    "auto_rebuild_enabled",
    "ensure_current",
    "release_lock",
]

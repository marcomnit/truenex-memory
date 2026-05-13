"""Local ingestion framework for structured source manifests."""

from truenex_memory.ingestion.manifest import SourceManifest, SourceEntry, IngestionRecord
from truenex_memory.ingestion.engine import ingest_manifest
from truenex_memory.ingestion.global_context import (
    ProjectContextReport,
    build_project_context,
    format_context_report,
)
from truenex_memory.ingestion.global_refresh import RefreshReport, refresh
from truenex_memory.ingestion.global_status import (
    GlobalStatusReport,
    build_global_status,
    format_status_report,
)
from truenex_memory.ingestion.global_auto_status import (
    AutoStatusReport,
    build_auto_status,
    format_auto_status_report,
)
from truenex_memory.ingestion.global_auto_memory import (
    AutoMemoryCandidate,
    generate_unverified_auto_memories,
)
from truenex_memory.ingestion.global_auto_review import (
    AutoMemoryReviewItem,
    AutoMemoryReviewReport,
    AutoMemorySourceSummary,
    build_auto_memory_review,
    format_auto_memory_review,
)
from truenex_memory.ingestion.global_search import (
    GlobalSearchHit,
    GlobalSearchReport,
    build_global_search,
    format_global_search_report,
)

__all__ = [
    "AutoMemoryCandidate",
    "AutoMemoryReviewItem",
    "AutoMemoryReviewReport",
    "AutoMemorySourceSummary",
    "AutoStatusReport",
    "GlobalStatusReport",
    "GlobalSearchHit",
    "GlobalSearchReport",
    "IngestionRecord",
    "ProjectContextReport",
    "RefreshReport",
    "SourceEntry",
    "SourceManifest",
    "build_auto_status",
    "build_auto_memory_review",
    "build_global_status",
    "build_global_search",
    "build_project_context",
    "format_auto_status_report",
    "format_auto_memory_review",
    "format_context_report",
    "format_status_report",
    "format_global_search_report",
    "generate_unverified_auto_memories",
    "ingest_manifest",
    "refresh",
]

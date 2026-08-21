"""Small data models returned by the local store."""

from __future__ import annotations

from dataclasses import dataclass
import json


VALID_STATUSES = frozenset({"active", "obsolete", "superseded", "conflicting", "unverified"})


@dataclass(frozen=True)
class MemoryNode:
    """A structured memory node row from the local store."""

    id: str
    project_id: str
    type: str
    title: str
    content: str
    status: str
    source_kind: str
    source_document_id: str | None
    source_chunk_id: str | None
    source_path: str | None
    content_hash: str | None
    created_by: str
    model_name: str | None
    confidence: float | None
    created_at: str
    updated_at: str
    # Id of the memory that replaced this one. Set together with the
    # `superseded` status, so a retired note can always be followed to
    # what now holds true instead of being a dead end.
    superseded_by: str | None = None


@dataclass(frozen=True)
class SearchHit:
    """A ranked local retrieval result."""

    title: str
    content: str
    source_path: str | None
    heading_path: str | None
    memory_type: str
    status: str
    score: float
    document_id: str | None = None
    source_id: str | None = None
    project: str | None = None
    created_at: str | None = None
    # Set for memory-node hits only. `document_id` is the *source* document
    # a memory was derived from and is NULL for hand-written memories, so it
    # cannot address the memory itself — without this field a truncated
    # memory result would be unresolvable.
    memory_id: str | None = None


@dataclass(frozen=True)
class RetrievalLog:
    """A recorded retrieval log row."""

    id: str
    project_id: str
    query: str
    top_k: int
    result_count: int
    results_json: str
    created_at: str

    def parsed_results(self) -> list[dict[str, object]]:
        return json.loads(self.results_json)

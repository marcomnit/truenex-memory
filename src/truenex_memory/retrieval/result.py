"""JSON helpers for retrieval results."""

from __future__ import annotations

from truenex_memory.store.models import SearchHit


def search_payload(query: str, results: list[SearchHit], *, trace_id: str | None = None) -> dict[str, object]:
    """Return the stable CLI/MCP search response shape."""

    return {
        "query": query,
        "results": [
            {
                "title": item.title,
                "content": item.content,
                "source_path": item.source_path,
                "heading_path": item.heading_path,
                "memory_type": item.memory_type,
                "status": item.status,
                "score": item.score,
            }
            for item in results
        ],
        "trace_id": trace_id,
    }

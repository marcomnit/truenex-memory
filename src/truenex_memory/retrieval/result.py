"""JSON helpers for retrieval results."""

from __future__ import annotations

from truenex_memory.store.models import SearchHit

# Excerpt budget for compact payloads. Matches DEFAULT_EXCERPT_CHARS in
# ingestion.global_search, so the two surfaces stay visually consistent.
DEFAULT_EXCERPT_CHARS = 320


def excerpt(content: str, max_chars: int = DEFAULT_EXCERPT_CHARS) -> str:
    """Collapse whitespace and ellipsize *content* to *max_chars*."""

    text = " ".join(content.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def search_payload(
    query: str,
    results: list[SearchHit],
    *,
    trace_id: str | None = None,
    full_content: bool = True,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
) -> dict[str, object]:
    """Return the stable CLI/MCP search response shape.

    With ``full_content=True`` (the default, preserving the historical
    shape) each result carries the verbatim chunk or memory body. Callers
    that pay per token — the MCP server above all — pass
    ``full_content=False`` to get a ``content_excerpt`` instead, plus the
    ``content_chars`` and ``truncated`` fields needed to decide whether to
    fetch the rest. A single result can exceed 4 KB verbatim, so the
    compact form typically cuts a five-result response by roughly 8x.

    ``document_id`` is always emitted: without it a truncated result would
    be unresolvable, and it is the key the ``memory_get`` drill-down takes.
    """

    def render(item: SearchHit) -> dict[str, object]:
        payload: dict[str, object] = {
            "title": item.title,
            "source_path": item.source_path,
            "heading_path": item.heading_path,
            "memory_type": item.memory_type,
            "status": item.status,
            "score": item.score,
            "document_id": item.document_id,
            "memory_id": item.memory_id,
        }
        if full_content:
            payload["content"] = item.content
        else:
            text = item.content or ""
            short = excerpt(text, excerpt_chars)
            payload["content_excerpt"] = short
            payload["content_chars"] = len(text)
            payload["truncated"] = len(" ".join(text.split())) > len(short)
        return payload

    return {
        "query": query,
        "results": [render(item) for item in results],
        "trace_id": trace_id,
    }

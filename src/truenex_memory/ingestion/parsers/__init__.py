"""Parser registry for ingestion source types."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from truenex_memory.ingestion.manifest import IngestionRecord

ParserFunc = Callable[[Path, str, str, str], list[IngestionRecord]]
"""Parser signature: (resolved_source_dir, project, source_tool, privacy_scope) -> records"""

_parsers: dict[str, ParserFunc] = {}


def register(source_type: str):
    """Decorator that registers a parser function for a source_type."""

    def decorator(parser: ParserFunc) -> ParserFunc:
        _parsers[source_type] = parser
        return parser

    return decorator


def get_parser(source_type: str) -> ParserFunc | None:
    return _parsers.get(source_type)


def parsers() -> dict[str, ParserFunc]:
    return dict(_parsers)


# Import parsers so they self-register
from truenex_memory.ingestion.parsers import text_docs  # noqa: E402, F401
from truenex_memory.ingestion.parsers import jsonl_sessions  # noqa: E402, F401

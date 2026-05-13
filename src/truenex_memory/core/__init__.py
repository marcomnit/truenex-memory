"""Core local primitives for Truenex Memory."""

from truenex_memory.core.chunker import TextChunk, chunk_text
from truenex_memory.core.config import MemoryConfig, MemoryPaths, ProjectConfig, resolve_project_config

__all__ = [
    "MemoryConfig",
    "MemoryPaths",
    "ProjectConfig",
    "TextChunk",
    "chunk_text",
    "resolve_project_config",
]

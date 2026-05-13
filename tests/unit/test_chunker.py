"""Tests for deterministic chunking."""

from truenex_memory.core.chunker import chunk_text


def test_chunk_text_tracks_markdown_heading() -> None:
    chunks = chunk_text("# Architecture\n\nWe use SQLite locally.", max_chars=200)

    assert len(chunks) == 1
    assert chunks[0].heading_path == "Architecture"
    assert chunks[0].token_count > 0
    assert len(chunks[0].content_hash) == 64


def test_chunk_text_ignores_empty_content() -> None:
    assert chunk_text("  \n\n ") == []

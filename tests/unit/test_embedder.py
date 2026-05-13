"""Tests for local embedding backends."""

import math

import pytest

from truenex_memory.core.embedder import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    HashingEmbedder,
    TARGET_EMBEDDING_MODEL,
)


def test_hashing_embedder_is_deterministic_and_normalized() -> None:
    embedder = HashingEmbedder(dimensions=32)

    first = embedder.embed_query("local multilingual memory")
    second = embedder.embed_query("local multilingual memory")

    assert first == second
    assert len(first) == 32
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)


def test_hashing_embedder_documents_use_target_model_metadata_without_downloads() -> None:
    embedder = HashingEmbedder()
    vectors = embedder.embed_documents(["alpha", "beta"])

    assert len(vectors) == 2
    assert len(vectors[0]) == DEFAULT_EMBEDDING_DIMENSIONS
    assert embedder.metadata.backend == "hashing"
    assert embedder.metadata.model_name == TARGET_EMBEDDING_MODEL
    assert embedder.metadata.requires_network is False
    assert embedder.metadata.downloads_model is False


def test_hashing_embedder_validates_inputs() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        HashingEmbedder(dimensions=0)

    with pytest.raises(ValueError, match="text"):
        HashingEmbedder(dimensions=8).embed_query("   ")

"""Local embedding primitives for offline retrieval tests."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Protocol


TARGET_EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
DEFAULT_EMBEDDING_DIMENSIONS = 384


@dataclass(frozen=True)
class EmbedderMetadata:
    """Metadata describing the local backend and intended production model."""

    backend: str
    model_name: str
    dimensions: int
    normalized: bool = True
    requires_network: bool = False
    downloads_model: bool = False


class LocalEmbedder(Protocol):
    """Protocol implemented by local, testable embedding backends."""

    @property
    def metadata(self) -> EmbedderMetadata:
        """Return backend metadata for diagnostics and vector-store setup."""

    def embed_query(self, text: str) -> list[float]:
        """Embed a retrieval query."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed one or more documents or chunks."""


class HashingEmbedder:
    """Deterministic local embedder that never downloads model weights.

    The metadata names ``intfloat/multilingual-e5-base`` as the target model so
    persisted vectors can declare their intended production replacement, while
    tests keep a small dependency-free backend.
    """

    def __init__(self, dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be greater than zero")
        self._metadata = EmbedderMetadata(
            backend="hashing",
            model_name=TARGET_EMBEDDING_MODEL,
            dimensions=dimensions,
        )

    @property
    def model_name(self) -> str:
        """Return a stable persisted model/backend identifier."""

        return f"{self.metadata.backend}-fallback:{self.metadata.model_name}"

    @property
    def dimensions(self) -> int:
        """Return embedding dimensionality."""

        return self.metadata.dimensions

    def embed(self, text: str) -> list[float]:
        """Embed text without query/passage prefixes for generic local retrieval."""

        _validate_text(text)
        return self._embed(text)

    @property
    def metadata(self) -> EmbedderMetadata:
        return self._metadata

    def embed_query(self, text: str) -> list[float]:
        _validate_text(text)
        return self._embed(f"query: {text}")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        for text in texts:
            _validate_text(text)
        return [self._embed(f"passage: {text}") for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.metadata.dimensions
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.metadata.dimensions
            sign = 1.0 if digest[8] % 2 == 0 else -1.0
            vector[index] += sign
        return _normalize(vector)


def _validate_text(text: str) -> None:
    if not text.strip():
        raise ValueError("text cannot be empty")


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"\w+", text, flags=re.UNICODE)]


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]

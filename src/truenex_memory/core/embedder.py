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


def sentence_transformers_model_name(model_name: str = TARGET_EMBEDDING_MODEL) -> str:
    """Persisted identifier for a sentence-transformers model.

    Name-only: lets callers (e.g. the reindex dry-run) know the persisted
    model name WITHOUT instantiating the model (no download).
    """

    return f"sentence-transformers:{model_name}"


class SentenceTransformerEmbedder:
    """Semantic embedder using sentence-transformers (optional dependency).

    Defaults to ``intfloat/multilingual-e5-base`` (768 dimensions). The e5
    family REQUIRES asymmetric prefixes: queries are embedded as
    ``"query: <text>"`` and documents as ``"passage: <text>"`` (the
    HashingEmbedder already mimicked them). The generic ``embed()`` keeps
    no prefix for backwards compatibility.
    """

    def __init__(
        self,
        model_name: str = TARGET_EMBEDDING_MODEL,
        *,
        device: str | None = None,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for semantic embeddings. "
                "Install with: pip install truenex-memory[semantic]"
            ) from exc
        if device is None:
            device = _default_device()
        self._model = SentenceTransformer(model_name, device=device)
        self._device = device
        # sentence-transformers >= 5.x renamed get_sentence_embedding_dimension
        # to get_embedding_dimension; support both.
        get_dim = getattr(self._model, "get_embedding_dimension", None) or getattr(
            self._model, "get_sentence_embedding_dimension"
        )
        dim = get_dim() or DEFAULT_EMBEDDING_DIMENSIONS
        self._metadata = EmbedderMetadata(
            backend="sentence-transformers",
            model_name=model_name,
            dimensions=dim,
            normalized=True,
            requires_network=False,
            downloads_model=True,
        )

    @property
    def metadata(self) -> EmbedderMetadata:
        return self._metadata

    @property
    def model_name(self) -> str:
        """Stable persisted identifier for vectors written by this backend.

        Persisted in ``chunks.embedding_model``; it must uniquely identify
        backend+model so vectors from other backends (e.g. hashing, 384d)
        are never mixed with this model's query vectors (768d).
        """

        return sentence_transformers_model_name(self.metadata.model_name)

    @property
    def dimensions(self) -> int:
        return self.metadata.dimensions

    @property
    def device(self) -> str:
        return self._device

    def embed(self, text: str) -> list[float]:
        _validate_text(text)
        return self._model.encode(text, convert_to_numpy=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        _validate_text(text)
        return self._model.encode(f"query: {text}", convert_to_numpy=True).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        for text in texts:
            _validate_text(text)
        embeddings = self._model.encode(
            [f"passage: {text}" for text in texts],
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [emb.tolist() for emb in embeddings]


def _default_device() -> str:
    """Pick "cuda" when a GPU is visible to torch, else "cpu"."""

    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


EMBEDDER_ENV_VAR = "TRUENEX_EMBEDDER"
EMBEDDER_CHOICES = ("hashing", "e5", "auto")


def embedder_from_env() -> HashingEmbedder | SentenceTransformerEmbedder:
    """Select the active embedder from the TRUENEX_EMBEDDER env var.

    - ``hashing`` (default): HashingEmbedder — current production behavior,
      no downloads, no semantic ranker;
    - ``e5``: SentenceTransformerEmbedder with intfloat/multilingual-e5-base
      (downloads ~1.1GB on first use, GPU if available);
    - ``auto``: e5 when sentence-transformers is importable, otherwise
      HashingEmbedder with a logged warning.

    Unknown values fall back to ``hashing`` with a logged warning.
    """

    import logging
    import os

    logger = logging.getLogger(__name__)
    choice = os.environ.get(EMBEDDER_ENV_VAR, "hashing").strip().lower()
    if choice == "hashing":
        return HashingEmbedder()
    if choice == "e5":
        return SentenceTransformerEmbedder()
    if choice == "auto":
        try:
            return SentenceTransformerEmbedder()
        except ImportError:
            logger.warning(
                "%s=auto but sentence-transformers is not installed; "
                "falling back to HashingEmbedder",
                EMBEDDER_ENV_VAR,
            )
            return HashingEmbedder()
    logger.warning(
        "invalid %s value %r (expected one of %s); falling back to HashingEmbedder",
        EMBEDDER_ENV_VAR,
        choice,
        EMBEDDER_CHOICES,
    )
    return HashingEmbedder()


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]

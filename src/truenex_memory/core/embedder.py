"""Local embedding primitives for offline retrieval tests."""

from __future__ import annotations

from dataclasses import dataclass
import gc
import hashlib
import logging
import math
import os
import re
import threading
import time
from typing import Protocol


logger = logging.getLogger(__name__)

TARGET_EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
DEFAULT_EMBEDDING_DIMENSIONS = 384

# Device dell'embedder semantico: "cpu" (difetto), "auto" (cuda se torch la
# vede, il vecchio comportamento) oppure un device torch esplicito ("cuda",
# "cuda:1"). Il difetto e' la CPU perche' ogni server MCP e' un processo a
# se' (uno per sessione Claude/Codex): sulla GPU ognuno teneva ~1,25 GB di
# VRAM dedicata per tutta la vita, sottraendola ad altre applicazioni locali
# (MedDesk e il suo LLM). Misurato il 10/11/2026 sull'archivio vero: con il
# modello gia' caricato una memory_search costa 1,2-3,6 s su CPU contro
# 0,25-0,5 s su GPU.
EMBED_DEVICE_ENV_VAR = "TRUENEX_EMBED_DEVICE"
DEFAULT_EMBED_DEVICE = "cpu"
# Secondi di inattivita' dopo i quali il modello condiviso viene scaricato
# (0 = mai). Ricaricarlo costa ~10 s alla prima ricerca dopo la pausa.
EMBED_IDLE_ENV_VAR = "TRUENEX_EMBED_IDLE_SECONDS"
DEFAULT_EMBED_IDLE_SECONDS = 900.0


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

    With ``idle_unload_seconds`` the weights are dropped after that much
    inactivity and reloaded transparently on the next encode, so a long-lived
    but idle process (an MCP server) does not hold the model's memory. With
    ``lazy`` nothing is loaded until the first encode or metadata access:
    with the dense ranker off a search only embeds the query in the
    no-lexical-hit fallback, so most server processes never need the model.
    """

    def __init__(
        self,
        model_name: str = TARGET_EMBEDDING_MODEL,
        *,
        device: str | None = None,
        idle_unload_seconds: float | None = None,
        lazy: bool = False,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for semantic embeddings. "
                "Install with: pip install truenex-memory[semantic]"
            ) from exc
        self._factory = SentenceTransformer
        self._model_name = model_name
        self._device = resolve_embed_device(device)
        self._idle_unload_seconds = (
            idle_unload_seconds if idle_unload_seconds and idle_unload_seconds > 0 else None
        )
        # RLock: encode e scarico non si sovrappongono mai, cosi' il timer
        # non toglie il modello a un encode in corso.
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._last_used = time.monotonic()
        self._load_count = 0
        self._model = None
        self._metadata: EmbedderMetadata | None = None
        if not lazy:
            with self._lock:
                self._ensure_model()

    @property
    def metadata(self) -> EmbedderMetadata:
        # Le dimensioni vengono dal modello: un'istanza pigra lo carica solo
        # quando qualcuno le chiede davvero, e le ricorda dopo lo scarico.
        if self._metadata is None:
            with self._lock:
                if self._metadata is None:
                    self._ensure_model()
        assert self._metadata is not None
        return self._metadata

    @property
    def model_name(self) -> str:
        """Stable persisted identifier for vectors written by this backend.

        Persisted in ``chunks.embedding_model``; it must uniquely identify
        backend+model so vectors from other backends (e.g. hashing, 384d)
        are never mixed with this model's query vectors (768d).
        """

        # Dal nome, non da metadata: leggerlo non deve caricare il modello.
        return sentence_transformers_model_name(self._model_name)

    @property
    def dimensions(self) -> int:
        return self.metadata.dimensions

    @property
    def device(self) -> str:
        return self._device

    @property
    def loaded(self) -> bool:
        """True while the model weights are resident in memory."""

        return self._model is not None

    @property
    def load_count(self) -> int:
        """How many times the weights were loaded (1 + reloads after idle)."""

        return self._load_count

    def embed(self, text: str) -> list[float]:
        _validate_text(text)
        with self._lock:
            try:
                return self._ensure_model().encode(text, convert_to_numpy=True).tolist()
            finally:
                self._last_used = time.monotonic()

    def embed_query(self, text: str) -> list[float]:
        _validate_text(text)
        with self._lock:
            try:
                model = self._ensure_model()
                return model.encode(f"query: {text}", convert_to_numpy=True).tolist()
            finally:
                self._last_used = time.monotonic()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        for text in texts:
            _validate_text(text)
        with self._lock:
            try:
                embeddings = self._ensure_model().encode(
                    [f"passage: {text}" for text in texts],
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
            finally:
                # Misurata dalla FINE dell'encode: un batch lungo non deve
                # risultare "inattivo" appena termina.
                self._last_used = time.monotonic()
        return [emb.tolist() for emb in embeddings]

    def unload(self) -> None:
        """Drop the weights now; the next encode reloads them."""

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._model is None:
                return
            self._model = None
        gc.collect()
        if self._device.startswith("cuda"):
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # pragma: no cover - torch/cuda runtime failure
                pass
        logger.info("embedder %s unloaded from %s", self._model_name, self._device)

    def _ensure_model(self):
        """Return the resident model, loading it if needed. Caller holds the lock."""

        self._last_used = time.monotonic()
        if self._model is None:
            self._model = self._factory(self._model_name, device=self._device)
            self._load_count += 1
            if self._metadata is None:
                # sentence-transformers >= 5.x renamed
                # get_sentence_embedding_dimension to get_embedding_dimension;
                # support both.
                get_dim = getattr(self._model, "get_embedding_dimension", None) or getattr(
                    self._model, "get_sentence_embedding_dimension"
                )
                self._metadata = EmbedderMetadata(
                    backend="sentence-transformers",
                    model_name=self._model_name,
                    dimensions=get_dim() or DEFAULT_EMBEDDING_DIMENSIONS,
                    normalized=True,
                    requires_network=False,
                    downloads_model=True,
                )
            if self._load_count > 1:
                logger.info("embedder %s reloaded on %s", self._model_name, self._device)
        self._schedule_idle_check(self._idle_unload_seconds)
        return self._model

    def _schedule_idle_check(self, delay: float | None) -> None:
        # Un solo timer vivo: l'uso lo lascia correre e il controllo, allo
        # scadere, riprogramma per il tempo che manca invece di creare un
        # timer per ogni encode.
        if delay is None or self._timer is not None:
            return
        timer = threading.Timer(delay, self._idle_check)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _idle_check(self) -> None:
        idle = self._idle_unload_seconds
        if idle is None:
            return
        with self._lock:
            self._timer = None
            if self._model is None:
                return
            remaining = idle - (time.monotonic() - self._last_used)
            if remaining > 0:
                self._schedule_idle_check(remaining)
                return
            # Dentro il lock: un encode arrivato fra il controllo e lo scarico
            # non deve vedersi togliere il modello appena usato.
            self.unload()


def _cuda_if_available() -> str:
    """Pick "cuda" when a GPU is visible to torch, else "cpu"."""

    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def resolve_embed_device(device: str | None = None) -> str:
    """Resolve an explicit device or ``TRUENEX_EMBED_DEVICE`` to a torch device.

    ``None`` reads the env var (default ``cpu``); ``auto`` becomes ``cuda``
    when torch sees a GPU, else ``cpu``; anything else is passed to torch as is.
    """

    raw = device if device is not None else os.environ.get(EMBED_DEVICE_ENV_VAR, "")
    choice = raw.strip().lower() or DEFAULT_EMBED_DEVICE
    if choice == "auto":
        return _cuda_if_available()
    return choice


def embed_idle_seconds_from_env() -> float | None:
    """Idle seconds before the shared model is unloaded; None means never."""

    raw = os.environ.get(EMBED_IDLE_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_EMBED_IDLE_SECONDS
    try:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(raw)
    except ValueError:
        logger.warning(
            "invalid %s value %r; using %s", EMBED_IDLE_ENV_VAR, raw, DEFAULT_EMBED_IDLE_SECONDS
        )
        return DEFAULT_EMBED_IDLE_SECONDS
    return value if value > 0 else None


# Un'istanza per processo: i tool MCP costruiscono un MemoryService a ogni
# chiamata, e senza questa cache ognuno ricaricava il modello da disco
# (~10 s per memory_search, misurato) lasciando sulla GPU la cache
# dell'allocatore di torch.
_SHARED_EMBEDDERS: dict[tuple[str, str, float | None], SentenceTransformerEmbedder] = {}
_SHARED_LOCK = threading.Lock()


def shared_sentence_transformer_embedder(
    model_name: str = TARGET_EMBEDDING_MODEL,
) -> SentenceTransformerEmbedder:
    """Process-wide embedder for ``model_name`` on the configured device."""

    key = (model_name, resolve_embed_device(), embed_idle_seconds_from_env())
    with _SHARED_LOCK:
        embedder = _SHARED_EMBEDDERS.get(key)
        if embedder is None:
            embedder = SentenceTransformerEmbedder(
                model_name, device=key[1], idle_unload_seconds=key[2], lazy=True
            )
            _SHARED_EMBEDDERS[key] = embedder
        return embedder


def clear_shared_embedders() -> None:
    """Unload and forget the process-wide embedders (tests, shutdown)."""

    with _SHARED_LOCK:
        embedders = list(_SHARED_EMBEDDERS.values())
        _SHARED_EMBEDDERS.clear()
    for embedder in embedders:
        embedder.unload()


EMBEDDER_ENV_VAR = "TRUENEX_EMBEDDER"
EMBEDDER_CHOICES = ("hashing", "e5", "auto")


def embedder_from_env() -> HashingEmbedder | SentenceTransformerEmbedder:
    """Select the active embedder from the TRUENEX_EMBEDDER env var.

    - ``hashing`` (default): HashingEmbedder — current production behavior,
      no downloads, no semantic ranker;
    - ``e5``: SentenceTransformerEmbedder with intfloat/multilingual-e5-base
      (downloads ~1.1GB on first use; device from TRUENEX_EMBED_DEVICE,
      CPU by default);
    - ``auto``: e5 when sentence-transformers is importable, otherwise
      HashingEmbedder with a logged warning.

    The semantic embedder is shared by the whole process (see
    ``shared_sentence_transformer_embedder``) and unloads itself after
    TRUENEX_EMBED_IDLE_SECONDS of inactivity.

    Unknown values fall back to ``hashing`` with a logged warning.
    """

    choice = os.environ.get(EMBEDDER_ENV_VAR, "hashing").strip().lower()
    if choice == "hashing":
        return HashingEmbedder()
    if choice == "e5":
        return shared_sentence_transformer_embedder()
    if choice == "auto":
        try:
            return shared_sentence_transformer_embedder()
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

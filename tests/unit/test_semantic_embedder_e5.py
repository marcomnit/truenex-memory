"""Tests for the e5 semantic embedder and embedder selection.

No model download: sentence-transformers is replaced by a fake module.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from truenex_memory.core.embedder import (
    EMBEDDER_ENV_VAR,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    TARGET_EMBEDDING_MODEL,
    embedder_from_env,
)


class _FakeArray:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class _FakeSentenceTransformer:
    """Records encode() calls; returns fixed 768-d vectors."""

    instances: list["_FakeSentenceTransformer"] = []

    def __init__(self, model_name: str, device: str | None = None) -> None:
        self.model_name = model_name
        self.device = device
        self.calls: list[object] = []
        _FakeSentenceTransformer.instances.append(self)

    def get_sentence_embedding_dimension(self) -> int:
        return 768

    def encode(self, text_or_texts, convert_to_numpy: bool = False, show_progress_bar: bool = False):
        self.calls.append(text_or_texts)
        if isinstance(text_or_texts, str):
            return _FakeArray([0.01] * 768)
        return [_FakeArray([0.01] * 768) for _ in text_or_texts]


@pytest.fixture()
def fake_st_module(monkeypatch: pytest.MonkeyPatch):
    _FakeSentenceTransformer.instances = []
    module = SimpleNamespace(SentenceTransformer=_FakeSentenceTransformer)
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return module


def _make(model_name: str | None = None) -> SentenceTransformerEmbedder:
    if model_name is None:
        return SentenceTransformerEmbedder(device="cpu")
    return SentenceTransformerEmbedder(model_name, device="cpu")


def test_e5_default_model_and_identity(fake_st_module) -> None:
    embedder = _make()

    assert embedder.metadata.model_name == TARGET_EMBEDDING_MODEL
    assert embedder.model_name == f"sentence-transformers:{TARGET_EMBEDDING_MODEL}"
    assert embedder.dimensions == 768
    assert embedder.device == "cpu"
    assert _FakeSentenceTransformer.instances[0].device == "cpu"


def test_e5_query_and_document_prefixes(fake_st_module) -> None:
    embedder = _make()
    model = _FakeSentenceTransformer.instances[0]

    query_vector = embedder.embed_query("cosa è il vault MedDesk")
    assert model.calls[-1] == "query: cosa è il vault MedDesk"
    assert len(query_vector) == 768

    doc_vectors = embedder.embed_documents(["primo chunk", "secondo chunk"])
    assert model.calls[-1] == ["passage: primo chunk", "passage: secondo chunk"]
    assert len(doc_vectors) == 2
    assert all(len(vector) == 768 for vector in doc_vectors)

    embedder.embed("testo generico")
    assert model.calls[-1] == "testo generico", "generic embed() keeps no prefix"


def test_e5_validates_inputs(fake_st_module) -> None:
    embedder = _make()
    with pytest.raises(ValueError, match="text"):
        embedder.embed_query("   ")
    with pytest.raises(ValueError, match="text"):
        embedder.embed_documents(["ok", "  "])


def test_embedder_from_env_defaults_to_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EMBEDDER_ENV_VAR, raising=False)
    assert isinstance(embedder_from_env(), HashingEmbedder)


def test_embedder_from_env_e5(monkeypatch: pytest.MonkeyPatch, fake_st_module) -> None:
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "e5")
    embedder = embedder_from_env()
    assert isinstance(embedder, SentenceTransformerEmbedder)
    assert embedder.metadata.model_name == TARGET_EMBEDDING_MODEL


def test_embedder_from_env_auto_with_st_available(
    monkeypatch: pytest.MonkeyPatch, fake_st_module
) -> None:
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "auto")
    assert isinstance(embedder_from_env(), SentenceTransformerEmbedder)


def test_embedder_from_env_auto_without_st_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # sys.modules entry set to None makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "auto")
    assert isinstance(embedder_from_env(), HashingEmbedder)


def test_embedder_from_env_invalid_value_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "turbo-9000")
    assert isinstance(embedder_from_env(), HashingEmbedder)

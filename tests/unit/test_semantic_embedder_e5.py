"""Tests for the e5 semantic embedder and embedder selection.

No model download: sentence-transformers is replaced by a fake module.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import truenex_memory.core.embedder as embedder_module
from truenex_memory.core.embedder import (
    DEFAULT_EMBED_IDLE_SECONDS,
    EMBED_DEVICE_ENV_VAR,
    EMBED_IDLE_ENV_VAR,
    EMBEDDER_ENV_VAR,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    TARGET_EMBEDDING_MODEL,
    clear_shared_embedders,
    embed_idle_seconds_from_env,
    embedder_from_env,
    resolve_embed_device,
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


@pytest.fixture(autouse=True)
def _isolated_shared_embedders(monkeypatch: pytest.MonkeyPatch):
    # The process-wide cache would otherwise carry a fake model from one test
    # into the next, and the user's env could pick the device.
    monkeypatch.delenv(EMBED_DEVICE_ENV_VAR, raising=False)
    monkeypatch.delenv(EMBED_IDLE_ENV_VAR, raising=False)
    clear_shared_embedders()
    yield
    clear_shared_embedders()


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


def _fake_hardware(
    monkeypatch: pytest.MonkeyPatch, *, cuda_devices: int = 0, mps: bool = False
) -> None:
    def available(kind: str, index: int | None = None) -> bool:
        if kind == "cuda":
            return cuda_devices > 0 and (index is None or index < cuda_devices)
        if kind == "mps":
            return mps and not index
        return False

    monkeypatch.setattr(embedder_module, "_accelerator_available", available)
    embedder_module._WARNED_DEVICES.clear()


# --- device, cache di processo e scarico per inattivita' -------------------


def test_device_defaults_to_cpu_even_with_a_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regressione 10/11/2026: ogni server MCP teneva ~1,25 GB di VRAM perche'
    # il difetto era "cuda se disponibile".
    _fake_hardware(monkeypatch, cuda_devices=2)
    assert resolve_embed_device() == "cpu"
    monkeypatch.setenv(EMBED_DEVICE_ENV_VAR, "auto")
    assert resolve_embed_device() == "cuda"
    monkeypatch.setenv(EMBED_DEVICE_ENV_VAR, " CUDA:1 ")
    assert resolve_embed_device() == "cuda:1"
    assert resolve_embed_device("cpu") == "cpu", "an explicit device wins over the env"


def test_embedder_from_env_uses_cpu_by_default(
    monkeypatch: pytest.MonkeyPatch, fake_st_module
) -> None:
    _fake_hardware(monkeypatch, cuda_devices=1)
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "e5")
    embedder = embedder_from_env()
    assert embedder.device == "cpu"
    embedder.embed_query("carica")
    assert _FakeSentenceTransformer.instances[0].device == "cpu"


def test_embedder_from_env_loads_the_model_once_per_process(
    monkeypatch: pytest.MonkeyPatch, fake_st_module
) -> None:
    # Regressione: i tool MCP creano un MemoryService a ogni chiamata e ogni
    # volta il modello veniva ricaricato da disco (~10 s per memory_search).
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "e5")
    first = embedder_from_env()
    second = embedder_from_env()
    assert first is second
    first.embed_query("uno")
    second.embed_query("due")
    embedder_from_env().embed_query("tre")
    assert len(_FakeSentenceTransformer.instances) == 1


def test_memory_service_reuses_the_shared_embedder(
    monkeypatch: pytest.MonkeyPatch, fake_st_module, tmp_path
) -> None:
    from truenex_memory.core.memory_service import MemoryService

    monkeypatch.setenv(EMBEDDER_ENV_VAR, "e5")
    a = MemoryService(tmp_path)
    b = MemoryService(tmp_path)
    assert a.embedder is b.embedder
    # Pigro: costruire il servizio (una volta per tool call MCP) non carica
    # il modello; con il ramo denso spento la ricerca lessicale non lo usa.
    assert _FakeSentenceTransformer.instances == []
    assert a.embedder.model_name == f"sentence-transformers:{TARGET_EMBEDDING_MODEL}"
    assert _FakeSentenceTransformer.instances == [], "model_name must not load"


def test_lazy_embedder_loads_on_metadata_or_encode(fake_st_module) -> None:
    embedder = SentenceTransformerEmbedder(device="cpu", lazy=True)
    assert not embedder.loaded and embedder.load_count == 0
    assert embedder.dimensions == 768
    assert embedder.loaded and embedder.load_count == 1
    embedder.unload()
    assert embedder.dimensions == 768 and not embedder.loaded, "dims are remembered"


def test_idle_seconds_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert embed_idle_seconds_from_env() == DEFAULT_EMBED_IDLE_SECONDS
    monkeypatch.setenv(EMBED_IDLE_ENV_VAR, "30")
    assert embed_idle_seconds_from_env() == 30.0
    monkeypatch.setenv(EMBED_IDLE_ENV_VAR, "0")
    assert embed_idle_seconds_from_env() is None
    monkeypatch.setenv(EMBED_IDLE_ENV_VAR, "presto")
    assert embed_idle_seconds_from_env() == DEFAULT_EMBED_IDLE_SECONDS
    for not_finite in ("inf", "nan", "-inf"):
        monkeypatch.setenv(EMBED_IDLE_ENV_VAR, not_finite)
        assert embed_idle_seconds_from_env() == DEFAULT_EMBED_IDLE_SECONDS


def test_unload_and_transparent_reload(fake_st_module) -> None:
    embedder = _make()
    assert embedder.loaded and embedder.load_count == 1

    embedder.unload()
    assert not embedder.loaded
    assert embedder.dimensions == 768, "metadata survives the unload"

    vector = embedder.embed_query("dopo la pausa")
    assert len(vector) == 768
    assert embedder.loaded and embedder.load_count == 2
    assert _FakeSentenceTransformer.instances[-1].calls[-1] == "query: dopo la pausa"


def test_idle_check_unloads_only_after_real_inactivity(
    monkeypatch: pytest.MonkeyPatch, fake_st_module
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(embedder_module.time, "monotonic", lambda: clock[0])
    embedder = SentenceTransformerEmbedder(device="cpu", idle_unload_seconds=60)
    try:
        clock[0] += 30
        embedder.embed_query("uso recente")
        clock[0] += 40  # 70 s dal caricamento, ma solo 40 dall'ultimo uso
        embedder._idle_check()
        assert embedder.loaded, "recent use must postpone the unload"

        clock[0] += 25  # 65 s dall'ultimo uso
        embedder._idle_check()
        assert not embedder.loaded
    finally:
        embedder.unload()


def test_idle_timer_fires_for_real(fake_st_module) -> None:
    import time as real_time

    embedder = SentenceTransformerEmbedder(device="cpu", idle_unload_seconds=0.05)
    try:
        deadline = real_time.monotonic() + 5
        while embedder.loaded and real_time.monotonic() < deadline:
            real_time.sleep(0.02)
        assert not embedder.loaded
        embedder.embed_query("riparte")
        assert embedder.loaded and embedder.load_count == 2
    finally:
        embedder.unload()


def test_no_idle_unload_without_a_timeout(fake_st_module) -> None:
    embedder = _make()
    assert embedder._timer is None


def test_idle_is_measured_from_the_end_of_a_long_encode(
    monkeypatch: pytest.MonkeyPatch, fake_st_module
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(embedder_module.time, "monotonic", lambda: clock[0])
    embedder = SentenceTransformerEmbedder(device="cpu", idle_unload_seconds=60)
    model = _FakeSentenceTransformer.instances[-1]
    original = model.encode

    def slow_encode(*args, **kwargs):
        clock[0] += 90  # un batch piu' lungo del timeout di inattivita'
        return original(*args, **kwargs)

    model.encode = slow_encode
    try:
        embedder.embed_documents(["chunk lungo"])
        embedder._idle_check()
        assert embedder.loaded, "a batch that just finished is not idle"
    finally:
        embedder.unload()


# --- macchine senza GPU (es. PC di sviluppo) e valori sbagliati -------------


@pytest.mark.parametrize("requested", ["cuda", "cuda:0", "CUDA:3", "mps"])
def test_unavailable_accelerator_falls_back_to_cpu(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, requested: str
) -> None:
    # Una config copiata da una workstation con GPU su un portatile normale
    # deve continuare a funzionare, non rompere ogni ricerca semantica.
    _fake_hardware(monkeypatch, cuda_devices=0)
    monkeypatch.setenv(EMBED_DEVICE_ENV_VAR, requested)
    with caplog.at_level("WARNING", logger=embedder_module.__name__):
        assert resolve_embed_device() == "cpu"
        assert resolve_embed_device() == "cpu"
    warnings = [r for r in caplog.records if "not available" in r.getMessage()]
    assert len(warnings) == 1, "one warning per value, not one per tool call"


def test_missing_gpu_index_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_hardware(monkeypatch, cuda_devices=1)
    assert resolve_embed_device("cuda:1") == "cpu"
    assert resolve_embed_device("cuda:0") == "cuda:0"


@pytest.mark.parametrize("bogus", ["gpu", "cuda:x", "vulkan", "cuda:-1"])
def test_invalid_device_falls_back_to_cpu(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bogus: str
) -> None:
    _fake_hardware(monkeypatch, cuda_devices=1)
    with caplog.at_level("WARNING", logger=embedder_module.__name__):
        assert resolve_embed_device(bogus) == "cpu"
    assert any("invalid embedder device" in r.getMessage() for r in caplog.records)


def test_auto_prefers_cuda_then_mps_then_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_hardware(monkeypatch, cuda_devices=1, mps=True)
    assert resolve_embed_device("auto") == "cuda"
    _fake_hardware(monkeypatch, cuda_devices=0, mps=True)
    assert resolve_embed_device("auto") == "mps"
    _fake_hardware(monkeypatch, cuda_devices=0, mps=False)
    assert resolve_embed_device("auto") == "cpu"


def test_accelerator_probe_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # torch assente: il probe deve dire "non disponibile", non sollevare.
    monkeypatch.setitem(sys.modules, "torch", None)
    assert embedder_module._accelerator_available("cuda") is False
    assert embedder_module._accelerator_available("mps") is False
    assert resolve_embed_device("auto") == "cpu"
    assert resolve_embed_device("cuda") == "cpu"


def test_shared_embedder_on_a_gpu_less_machine_with_cuda_configured(
    monkeypatch: pytest.MonkeyPatch, fake_st_module
) -> None:
    _fake_hardware(monkeypatch, cuda_devices=0)
    monkeypatch.setenv(EMBEDDER_ENV_VAR, "e5")
    monkeypatch.setenv(EMBED_DEVICE_ENV_VAR, "cuda")
    embedder = embedder_from_env()
    assert len(embedder.embed_query("funziona lo stesso")) == 768
    assert embedder.device == "cpu"
    assert _FakeSentenceTransformer.instances[0].device == "cpu"

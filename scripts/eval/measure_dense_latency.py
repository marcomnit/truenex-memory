"""Misura latenza denso GPU vs OFF sul DB live (Stadio attivazione, READ-ONLY).

Fasi (log incrementale su %TEMP%\\measure_dense_latency.log, a prova di kill):
  enc    — encoding e5-base su GPU: warmup escluso, 25 query x 3 rep
  off    — search end-to-end con HashingEmbedder (produzione OFF), 25 query
  load   — caricamento vector index reale (una tantum, separato)
  on     — search end-to-end con e5 GPU + gate coseno, 25 query
  prof   — profiling componenti su 3 query (embed/validazione/matvec/hydration)

Uso: python measure_dense_latency.py [fasi...]   default: tutte in ordine.
TRUENEX_EMBEDDER=e5 vive solo in questo processo; il DB non viene scritto
(MemoryRepository.search scrive retrieval_logs: usiamo le stesse chiamate
dell'harness eval, comportamento gia' accettato negli stadi precedenti).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

DB = Path(r"C:\Users\marco\.truenex-memory\truenex_memory.db")
LOG = Path(os.environ["TEMP"]) / "measure_dense_latency.log"
QUERIES = [
    c["query"]
    for c in json.loads((ROOT / "scripts" / "eval" / "queries.json").read_text(encoding="utf-8"))["cases"]
]

log_f = open(LOG, "a", encoding="utf-8")


def log(msg: str) -> None:
    print(msg, flush=True)
    log_f.write(msg + "\n")
    log_f.flush()


def pct(values: list[float]) -> dict[str, float]:
    s = sorted(values)
    return {
        "mean": round(statistics.fmean(s) * 1000, 1),
        "p50": round(statistics.median(s) * 1000, 1),
        "p95": round(s[min(int(len(s) * 0.95), len(s) - 1)] * 1000, 1),
        "max": round(s[-1] * 1000, 1),
    }


phases = sys.argv[1:] or ["enc", "off", "load", "on", "prof"]
log(f"\n=== run {time.strftime('%H:%M:%S')} fasi={phases} ===")

# ---- embedder (una sola istanza, device esplicitato nel log) ----
t0 = time.perf_counter()
from truenex_memory.core.embedder import SentenceTransformerEmbedder, _default_device  # noqa: E402

device = _default_device()
embedder = SentenceTransformerEmbedder()
log(f"embedder load: {time.perf_counter()-t0:.1f}s | _default_device()={device} | "
    f"model device={next(embedder._model.parameters()).device}")

from truenex_memory.store.repository import MemoryRepository  # noqa: E402

if "enc" in phases:
    for q in QUERIES[:3]:  # warmup
        embedder.embed_query(q)
    times = []
    for _rep in range(3):
        for q in QUERIES:
            t0 = time.perf_counter()
            embedder.embed_query(q)
            times.append(time.perf_counter() - t0)
    log(f"[enc] encoding e5 GPU per-query (n={len(times)}, warmup escluso): {pct(times)} ms")

if "off" in phases:
    from truenex_memory.retrieval.semantic import HashingEmbedder  # noqa: E402

    repo_off = MemoryRepository(DB, embedder=HashingEmbedder(), project_id="default")
    repo_off.search(QUERIES[0], top_k=5)  # warmup conn/FTS
    times = []
    for q in QUERIES:
        t0 = time.perf_counter()
        repo_off.search(q, top_k=5)
        times.append(time.perf_counter() - t0)
    log(f"[off] search e2e hashing (n={len(times)}): {pct(times)} ms")

os.environ["TRUENEX_EMBEDDER"] = "e5"

# Iniezione npy: il load reale e' misurato a parte (build incrementale,
# vedi log); qui serve l'indice caldo per le misure per-query.
if os.environ.get("MEASURE_USE_NPY") == "1":
    import numpy as np  # noqa: E402

    from truenex_memory.retrieval import vector_index  # noqa: E402
    from truenex_memory.retrieval.vector_index import VectorIndexEntry  # noqa: E402
    from truenex_memory.store.sqlite import connect as _connect  # noqa: E402

    NPY = Path(os.environ["TEMP"]) / "truenex_npy"
    MODEL = "sentence-transformers:intfloat/multilingual-e5-base"
    t0 = time.perf_counter()
    parts = sorted(NPY.glob("part_*.npy"))
    matrix = np.concatenate([np.load(p) for p in parts])
    point_ids: list[str] = []
    for i in range(len(parts)):
        point_ids.extend(json.loads((NPY / f"ids_{i}.json").read_text()))
    with _connect(DB) as _conn:
        _max_upd = vector_index._model_max_updated(_conn, MODEL)
    vector_index._CACHE[(str(DB), MODEL)] = VectorIndexEntry(
        vector_count=len(point_ids), max_updated_at=_max_upd,
        point_ids=point_ids, matrix=matrix,
    )
    log(f"[npy] indice iniettato da npy: {matrix.shape[0]} vettori in {time.perf_counter()-t0:.1f}s")

repo_on = MemoryRepository(DB, embedder=embedder, project_id="default")

if "load" in phases or "on" in phases or "prof" in phases:
    from truenex_memory.retrieval.vector_index import get_index  # noqa: E402
    from truenex_memory.store.sqlite import connect  # noqa: E402

    t0 = time.perf_counter()
    with connect(DB) as conn:
        entry = get_index(DB, conn, embedder.model_name)
    log(f"[load] vector index load REALE (una tantum): {time.perf_counter()-t0:.1f}s | "
        f"vectors={entry.vector_count} matrix_RAM={entry.matrix.nbytes/1e9:.2f}GB")

if "on" in phases:
    t0 = time.perf_counter()
    repo_on.search(QUERIES[0], top_k=5)
    log(f"[on] prima search a caldo (validazione+pages): {time.perf_counter()-t0:.2f}s")
    times = []
    for q in QUERIES:
        t0 = time.perf_counter()
        repo_on.search(q, top_k=5)
        times.append(time.perf_counter() - t0)
        log(f"  on query {q[:40]!r}: {times[-1]*1000:.0f}ms")
    log(f"[on] search e2e e5-GPU gated (n={len(times)}): {pct(times)} ms")

if "prof" in phases:
    from truenex_memory.retrieval.vector_index import get_index, search_index  # noqa: E402
    from truenex_memory.store.repository import _hydrate_chunks_by_point_ids  # noqa: E402
    from truenex_memory.store.sqlite import connect  # noqa: E402

    conn = connect(DB)
    for q in QUERIES[:3]:
        for rep in range(3):
            t0 = time.perf_counter(); qv = embedder.embed_query(q); t_e = time.perf_counter() - t0
            t0 = time.perf_counter(); entry = get_index(DB, conn, embedder.model_name); t_g = time.perf_counter() - t0
            t0 = time.perf_counter(); matches = search_index(entry, qv, 100); t_m = time.perf_counter() - t0
            t0 = time.perf_counter(); _hydrate_chunks_by_point_ids(conn, [m.point_id for m in matches]); t_h = time.perf_counter() - t0
            t0 = time.perf_counter(); repo_on._search_semantic_chunks(conn, q, 100); t_s = time.perf_counter() - t0
            log(f"[prof] {q[:35]!r} rep{rep}: embed={t_e*1000:.0f} validazione={t_g*1000:.1f} "
                f"matvec={t_m*1000:.0f} hydration={t_h*1000:.0f} semantic_tot={t_s*1000:.0f} ms")
    conn.close()

log("=== fine ===")
log_f.close()

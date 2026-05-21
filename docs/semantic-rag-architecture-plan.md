# Semantic RAG Architecture Plan

**Status:** Draft — awaiting Codex review  
**Scope:** Replace keyword-only retrieval with hybrid semantic + lexical RAG  
**Goal:** Make Truenex Memory chat retrieval production-ready for commercial sale  
**Estimated effort:** 2–3 days  
**Breaking changes:** None (backward-compatible lazy migration)

---

## Problem Statement

The current retrieval stack (`HashingEmbedder` + BM25) is lexical-only. It cannot distinguish between:
- A design document **explaining** what a Recursive Orchestrator is
- A backend source file **mentioning** the words "recursive orchestrator" in a system prompt
- A workflow document discussing Codex handoffs that happens to contain the word "orchestrator"

This produces source lists where 7 of 9 items are irrelevant — unacceptable for a paid product.

---

## Target Architecture

### 1. Embedder Abstraction Layer

```
BaseEmbedder (abstract)
├── HashingEmbedder        # default, zero deps, backward-compatible
└── SentenceTransformerEmbedder  # optional, high quality
```

- `HashingEmbedder` remains the default for users who install `truenex-memory` without extras.
- `SentenceTransformerEmbedder` is activated when `pip install truenex-memory[semantic]` is used or when `config.yaml` points to a model name.
- Runtime check: if the configured embedder fails to load, fall back to `HashingEmbedder` with a warning.

### 2. DB Schema (SQLite)

Add to `chunks` table:
```sql
ALTER TABLE chunks ADD COLUMN embedding BLOB;
```

- `BLOB` stores a JSON-serialised float array (or compact binary float32).
- SQLite `sqlite-vec` extension is **not** required for Phase 1; similarity is computed in Python over the top-50 BM25 candidates. This keeps deployment simple.
- Future Phase 2 can migrate to `sqlite-vec` for native vector indexing if scale demands it.

### 3. Indexing Pipeline

During `svc.index()` or `global refresh`:
1. Chunk the document (existing logic).
2. For each chunk, generate embedding via the active embedder.
3. Store embedding in the `embedding` BLOB column.

**Backward compatibility:**
- Existing chunks (42K in production DB) have `embedding IS NULL`.
- A lazy migration path computes missing embeddings on first query and persists them.
- An explicit CLI command `truenex-mem migrate embeddings` allows batch migration.

### 4. Retrieval Pipeline (chat_engine.py)

Three-phase retrieval:

**Phase 1 — Lexical recall (fast, broad)**
- BM25 over project-restricted doc_ids, top 50 candidates.
- Filters: exclude `/tests/`, meta-files, etc. (existing logic).

**Phase 2 — Semantic re-ranking (quality)**
- Load embeddings for the 50 candidates.
- Compute cosine similarity between query embedding and each candidate embedding.
- Combine: `final_score = bm25_score * 0.4 + cosine_score * 0.6`.
- Sort by `final_score`.

**Phase 3 — Deduplication & thresholding**
- Deduplicate by `source_path`, keep best score per document.
- Adaptive threshold: drop items whose cosine_score < 0.4 * max_cosine.
- Return top 6 distinct documents.

### 5. Context Assembly (unchanged philosophy)

- Foundation docs (AGENTS.md, README.md, docs/*.md) still read from filesystem.
- Search results still capped and appended.
- The improvement is purely in **which documents are selected**, not in how they are formatted.

---

## Technology Choices

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Default embedder | `HashingEmbedder` | Zero dependencies, works offline, backward-compatible |
| Semantic embedder | `sentence-transformers` + `all-MiniLM-L6-v2` | 22M params, ~90MB, Apache-2.0, proven in production RAG systems |
| Alternative embedder | ONNX Runtime + quantised MiniLM | ~30MB, faster inference, harder packaging (future option) |
| Vector storage | SQLite BLOB + Python similarity | Simplest deployment, no extra services |
| Future index | `sqlite-vec` or `faiss-cpu` | If dataset grows beyond ~200K chunks |

---

## Migration Strategy

### Lazy migration (default)
```python
# In _search_project_chunks()
for row in bm25_candidates:
    if row["embedding"] is None:
        emb = embedder.embed(row["content"])
        db.update_chunk_embedding(row["id"], emb)
        row["embedding"] = emb
```
- First query after upgrade is slower (embeds missing chunks on demand).
- Subsequent queries are fast.
- No downtime, no data loss.

### Batch migration (optional CLI)
```bash
truenex-mem migrate embeddings
```
- Iterates all chunks with NULL embedding.
- Batch-computes in chunks of 100 to keep memory bounded.
- Progress bar + ETA.

---

## Files to Modify

| File | Change |
|------|--------|
| `src/truenex_memory/core/embedder.py` | Add `SentenceTransformerEmbedder` class; keep `HashingEmbedder` |
| `src/truenex_memory/store/sqlite.py` | Add `embedding` column; add `update_chunk_embedding()` |
| `src/truenex_memory/ingestion/chunker.py` or indexer | Compute and store embedding during indexing |
| `src/truenex_memory/core/chat_engine.py` | Rewrite `_search_project_chunks()` with 3-phase retrieval |
| `src/truenex_memory/cli/main.py` | Add `migrate embeddings` subcommand |
| `pyproject.toml` | Add `[semantic]` extra with `sentence-transformers` dependency |

---

## Test Plan

1. **Unit test** — `test_semantic_reranking.py`  
   Mock embeddings, verify that a document with matching keywords but wrong semantics is demoted.

2. **Integration test** — Index a sample project, query "recursive orchestrator", assert that `recursive-orchestrator-design.md` is ranked above `llm_client.py` and workflow docs.

3. **Migration test** — Start with legacy DB (no embeddings), run query, verify lazy migration produces correct results and persists embeddings.

4. **Fallback test** — Uninstall `sentence-transformers`, verify system falls back to `HashingEmbedder` gracefully.

5. **Performance test** — Query latency with 50K chunks must remain < 500ms on a mid-range laptop.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| `sentence-transformers` adds 90MB download | Marked as optional extra; default install remains lightweight |
| First query after upgrade is slow (lazy migration) | Document in release notes; provide batch CLI command |
| ONNX packaging complexity | Deferred to future phase; start with sentence-transformers |
| CPU inference too slow on old hardware | Batch size 1, model is tiny (22M params), typically < 10ms per chunk on CPU |
| Backward compatibility breaks | Lazy migration ensures old DBs work without changes |

---

## Out of Scope (Phase 2)

- Cross-encoder re-ranking (better quality, more latency).
- `sqlite-vec` native vector index (needed only at >200K chunks).
- Multi-modal embeddings (images, PDFs).
- GPU inference (overkill for this scale).

---

## Decision Required

**Approve this plan?** If yes:
1. Kimi implements the core change.
2. Codex reviews the diff.
3. Tests are run end-to-end.
4. Commit with tag `v0.3.0a1`.

If changes are requested, iterate on this document before touching code.

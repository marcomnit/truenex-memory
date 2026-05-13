# Roadmap

## 0.1.0 — Public Alpha (current)

- [x] Local project memory: add, search, list, lifecycle status.
- [x] CLI (`truenex-mem`) with full command surface.
- [x] MCP stdio server for agent integration.
- [x] Source ingestion engine with manifest-driven indexing.
- [x] Global store: catalog, ledger, cross-project search.
- [x] BM25 + semantic retrieval with local embedding fallback.
- [x] Schema migration with backup/restore.
- [x] Export/import (readable JSON).
- [x] Auto-memory: lifecycle controls, dedup, review pipeline.
- [x] Privacy diagnostics (`doctor --privacy`).
- [x] Adaptive task tracking with outcome recording.
- [x] Agent discovery bootstrap (`CLAUDE.md` / `AGENTS.md` adapters).

## 0.2.0 — Stabilization

- [ ] Public test coverage for all CLI commands.
- [ ] Public package build and clean-install validation.
- [ ] Release artifacts with hashes.
- [ ] Qdrant integration hardening.
- [ ] Chunking configurability.
- [ ] Better source exclusion presets.
- [ ] Documentation site or expanded docs index.

## 0.3.0 — Ecosystem

- [ ] Plugin system for custom embedders and stores.
- [ ] Additional agent platform adapters.
- [ ] Safer multi-project merge/import workflows.
- [ ] Performance: incremental indexing, lazy chunk loading.

## Beyond

- Optional local UI dashboard for global store browsing.
- Team collaboration features (separate distribution, not part of the
  Apache-2.0 core).
- Optional sync/governance features, only if local data ownership remains
  preserved.

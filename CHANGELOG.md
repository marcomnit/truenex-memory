# Changelog

## [0.1.0-alpha.1] — Unreleased

### Core

- Local project memory with add, search, list, and lifecycle status
  (active / obsolete / superseded / conflicting / unverified).
- CLI entry point `truenex-mem` via Typer with full `--help` tree.
- MCP stdio server exposing `memory_search`, `memory_add`,
  `global_status`, and `global_project_context`.
- Export and import as readable JSON.

### Storage

- SQLite metadata store with schema migration (v1 → v4).
- Migration backup/restore safety net.
- Global store: source catalog, ledger, cross-project document index.

### Ingestion

- Manifest-driven source ingestion with dry-run validation.
- Global refresh pipeline with health checks.
- Source health cleanup for stale catalog entries.

### Retrieval

- BM25 lexical scoring (schema v4).
- Semantic retrieval with deterministic local embedding fallback.
- Configurable top-k and source-type boosting.

### Auto Memory

- Global auto-memory extraction from agent session logs.
- Lifecycle controls: review, approve, reject, promote, and prune.
- Dedup with separate handling for active, unverified, and
  rejected/tombstoned candidates.
- Fast skip-refresh mode and local telemetry counters in command output.

### Adapters

- `CLAUDE.md` and `AGENTS.md` file adapters for agent discovery.
- JSONL session parser with per-exchange chunking.
- Noise filter for agent session transcripts.

### Platform

- Python >= 3.12, Apache-2.0 license.
- Optional Qdrant support (fails closed when unavailable).
- `doctor --privacy` confirms no cloud, no telemetry, no automatic upload.
- Adaptive task tracking with verifier rounds.

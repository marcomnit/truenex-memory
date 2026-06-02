# Changelog

## [Unreleased]

### Added

## [0.2.0a2] — 2026-06-02

### Fixed

- Added missing `httpx>=0.27` to main `project.dependencies`. `truenex-mem serve` no longer crashes with `ModuleNotFoundError: No module named 'httpx'` on a clean install.

## [0.2.0a1] — 2026-05-19

### Fixed

- README/docs: corrected installation instructions (alpha is source-only, PyPI coming with v0.2.0 stable).
- `serve.py`: added missing `GET /api/file-analysis` endpoint.
- `scripts/build_release.py`: version now read dynamically from `pyproject.toml`.
- `ROADMAP.md`: rewritten as operational execution plan with priorities and criteria of done.

### Stabilization

- Test coverage expanded to >80% for all CLI commands.
- Package build hardened: `pip install -e .` and `pip install .` verified clean.
- Release artifact script with SHA-256 hashes (`scripts/build_release.py`).
- Qdrant integration hardened: configurable timeout and retries, graceful fallback to SQLite with logging.
- Chunking configurability: `--chunk-size` and `--chunk-overlap` CLI options, plus `TRUENEX_MEMORY_CHUNK_SIZE` / `TRUENEX_MEMORY_CHUNK_OVERLAP` env vars.
- Unified source exclusion presets (node_modules, .git, __pycache__, build/, dist/) with `.gitignore` awareness.
- Expanded documentation: PyPI install instructions, troubleshooting guide.

## [0.1.0-alpha.1] — 2026-05-13

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

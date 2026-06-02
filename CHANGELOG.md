# Changelog

## [Unreleased]

### Added

## [0.2.1] — 2026-06-02

### Added

- `truenex-mem update self` — auto-detects pipx/pip and upgrades to latest version.
- Auto-update notice at CLI startup — checks PyPI every 24h (cache + 2s timeout) and warns if a newer version exists.
- Upgrade instructions in README and docs/installation.md.

### Fixed

- Include missing `self_update.py` and `auto_update_check.py` in the package (were untracked during v0.2.0 build).

## [0.2.0] — 2026-06-02

### Added

- **Pro license enforcement**: commands now require a Pro license (activated via `truenex-mem license activate`):
  - `truenex-mem serve` — Desktop App backend
  - `truenex-mem global auto run|status|review|approve|reject|promote|prune`
  - `truenex-mem git init|push|pull|status|remote`
- **Git Bridge** (`truenex-mem git *`): sync project memory across multiple PCs via Git.
  - `git init` — initialise sync repo with `.gitignore`
  - `git push` — export DB → JSON and push to remote
  - `git pull` — pull from remote and import JSON → DB
  - `git status` — show sync state (DB vs exported JSON vs git dirty)
  - `git remote add|remove|list|show` — manage remotes
  - All git commands support `--json` and `--dry-run`.
- **MCP setup guide** (`docs/mcp-setup.md`): copy-paste configuration snippets for Claude Code, Kimi, Cursor, and Codex.
- **Landing page** (`memory.truenex.ai`): quickstart with pipx, commands reference, and MCP setup link.

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

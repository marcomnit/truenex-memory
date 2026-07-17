# Changelog

## [Unreleased]

## [0.4.0] — 2026-07-17

### Added

- Persistent SQLite FTS5 index for document chunks, including automatic backfill and insert/update/delete triggers.
- Database schema v5 with safe migration and backup support.
- Search API filters and richer source, project, and timestamp metadata in results.
- Optional local LLM bootstrap tooling, Docker configuration, and setup documentation.
- Release workflow for tagged builds, GitHub Releases, and PyPI Trusted Publishing.

### Changed

- Lexical evidence is evaluated before dense fallback so partially embedded databases cannot hide exact matches.
- Global and project search use the persistent FTS5 index, with the previous Python BM25 implementation retained as a compatibility fallback.
- Agent discovery resolves manifests and preferences from the explicitly selected home directory.

### Fixed

- `memory_search` no longer scans the entire chunk corpus in Python for every query.
- Ingestion metadata preambles are no longer exposed as search results.
- Search results suppress operating-system trash paths and collapse identical mirrored chunks.
- Retrieval gating now follows the most recent ledger status for each source path.
- Chat returns a controlled service-unavailable response when the local memory database cannot be opened.

### Security

- Local activation databases, `.env` files, and private signing keys are explicitly excluded from version control.

## [0.2.7] — 2026-06-03

### Fixed

- Git Bridge `pull` now uses `--allow-unrelated-histories` to support syncing between two independently initialized PCs.

## [0.2.6] — 2026-06-03

### Fixed

- Git Bridge `push` and `pull` now run with `capture_output=False`, allowing Git Credential Manager to prompt for credentials interactively (previously stdout/stderr were captured by Python, blocking credential prompts).

## [0.2.5] — 2026-06-03

### Fixed

- Git Bridge bug: `MemoryRepository(str(db_path))` caused `AttributeError` because `sqlite.connect()` expected a `Path` object. Removed unnecessary `str()` calls in `git_commands.py` and made `sqlite.connect()` robust to both `Path` and `str` inputs.

## [0.2.4] — 2026-06-03

### Fixed

- `__init__.py` now reads `__version__` dynamically from package metadata (`importlib.metadata`) instead of a hardcoded string.
- `licensing.py` uses server-provided `expires_at` during activation instead of locally decoding the JWT token. Fixes cases where `expires` was incorrectly reported as `never` on some Windows machines.

## [0.2.3] — 2026-06-03

### Fixed

- Bump `__version__` to match package metadata (was still reporting 0.2.1 internally).

## [0.2.2] — 2026-06-03

### Fixed

- **License enforcement is now real**: previously a license key could be copied to unlimited devices. This was a critical commercial bug.
  - Added online license server (`memory.truenex.ai/api/v1/license`) with device binding (max 3 devices per Pro key).
  - Activation now issues an RS256 JWT token tied to a stable device fingerprint.
  - Offline validation works for 30 days; 7-day grace period after expiry.
  - `truenex-mem license deactivate` frees a device slot on the server.

### Changed

- `truenex-mem license activate` no longer supports `--offline`, `--tier`, `--expires-at`, or `--feature` flags. Tier and expiry are server-side.

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

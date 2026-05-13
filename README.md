# Truenex Memory

Local-first memory layer for coding agents.

**Status: public alpha.** The open-source core is ready for technical users who
want a local, inspectable memory store for Codex, Claude Code, Cursor, and other
MCP-aware coding agents. APIs and command output may still change before a
stable 1.0 release.

## Why It Exists

Coding agents lose context between sessions. Truenex Memory gives them a local
source of truth they can query before making claims, proposing changes, or
repeating work already done.

It stores project decisions, indexed documents, source chunks, provenance,
retrieval logs, and generated memory candidates on your machine.

Core principles:

- Local-first: no account is required.
- Privacy-first: no automatic upload of code, documents, or memory.
- Agent-first: CLI and MCP are primary surfaces.
- Data ownership: export/import is part of the core.
- Open core: the Apache-2.0 local core remains useful without paid services.

## What Is Included In The Open-Source Core

- `truenex-mem` CLI.
- Local SQLite store.
- Deterministic local fallback embeddings for offline use.
- Optional local Qdrant backend.
- Project indexing and retrieval.
- Manual memory add/list/search/status commands.
- Retrieval traces and logs.
- Export/import.
- Schema migration with local backups and restore.
- Source manifest ingestion.
- Agent discovery and confirmed source catalog.
- Incremental global refresh and global status.
- Read-only global project context/bootstrap.
- Conservative Auto Memory candidate generation and manual review lifecycle.
- MCP stdio server for agent integrations.

Optional future Pro/Team features may add advanced UI, team workflows, sync, or
governance. They must remain opt-in and must not lock local data.

## Installation

Development install from a checkout:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
truenex-mem --help
```

For a clean user-style install after release artifacts are published:

```bash
pipx install truenex-memory
```

Until public package artifacts are available, use the editable install above.

## 5-Minute Quickstart

Run these commands from a project root:

```bash
truenex-mem doctor --privacy
truenex-mem init
truenex-mem add "We use SQLite for local metadata" --type decision
truenex-mem list
truenex-mem search "local metadata"
truenex-mem index examples/sample_project
truenex-mem search "which vector database is planned?"
truenex-mem export --output memory-export.json
```

Expected results:

- `doctor --privacy` reports local paths and no cloud/telemetry upload.
- `init` creates `.truenex-memory/`.
- `add` returns a local memory id.
- `search` returns local results with score and source metadata.
- `index` reads the sample project.
- `export` writes a readable JSON export.

## Sample Project

The repository includes a sample project:

```text
examples/sample_project/
  README.md
  docs/
    architecture.md
    decisions.md
```

Try:

```bash
truenex-mem index examples/sample_project
truenex-mem search "MCP transport"
truenex-mem search "automatic upload"
truenex-mem search "local metadata database"
```

## MCP stdio

Run the local MCP server from a project root:

```bash
truenex-mem mcp --project-root .
```

Example agent configuration:

```json
{
  "mcpServers": {
    "truenex-memory": {
      "command": "truenex-mem",
      "args": ["mcp", "--project-root", "."]
    }
  }
}
```

The server exposes:

- `memory_search(query, top_k = 5)`
- `memory_add(content, memory_type = "note")`
- `global_status(home?, catalog?, db?)`
- `global_project_context(project, home?, catalog?, db?, limit = 20)`

Global MCP tools are read-only bootstrap helpers. They read the confirmed local
catalog and global database; server aliases are reported as hints only and are
never executed by the core.

## Global Memory Workflow

Truenex Memory can build a confirmed global catalog from local agent and project
sources:

```bash
truenex-mem global discover --from-agents --output discovery.md
truenex-mem global sources review --output sources-preview.json
truenex-mem global sources confirm --input sources-preview.json
truenex-mem global refresh
truenex-mem global auto status
```

Daily local refresh:

```bash
truenex-mem global auto run
truenex-mem global search "project release status" --kind all
```

Generated Auto Memory remains conservative: candidates are `unverified` until a
user approves, rejects, promotes, or prunes them.

## CLI Reference

```bash
truenex-mem init
truenex-mem add "content" --type decision
truenex-mem list [--status active] [--json]
truenex-mem status set <memory-id> obsolete
truenex-mem index .
truenex-mem search "query" [--include-inactive]
truenex-mem logs [--limit 20] [--json]
truenex-mem trace show <trace-id> [--json]
truenex-mem migrate status [--json]
truenex-mem migrate apply [--json]
truenex-mem migrate backup-list [--json]
truenex-mem migrate restore <backup-filename> [--json]
truenex-mem ingest manifest --manifest memory-sources.json [--dry-run] [--json]
truenex-mem global sources add --source-type document --path-or-alias <path> [--yes] [--json]
truenex-mem global search "query" [--top-k 10] [--kind all|memory|chunks] [--include-inactive] [--json]
truenex-mem global auto run [--dry-run] [--auto-memory] [--auto-memory-limit 50] [--auto-memory-per-source-limit 5] [--json]
truenex-mem global auto status [--stability-seconds 120] [--json]
truenex-mem global auto review [--limit 20] [--source README] [--json]
truenex-mem global auto approve <memory-id> [--json]
truenex-mem global auto reject <memory-id> [--json]
truenex-mem global auto prune [--source README] [--limit 100] [--yes] [--json]
truenex-mem doctor --privacy
truenex-mem export --output memory-export.json
truenex-mem import memory-export.json
truenex-mem mcp --project-root .
```

See [docs/ingestion.md](docs/ingestion.md) for source manifest ingestion.
See [docs/agent-discovery-bootstrap.md](docs/agent-discovery-bootstrap.md) for
global discovery and bootstrap design.
See [docs/phase-3-auto-memory-design.md](docs/phase-3-auto-memory-design.md)
for the Auto Memory design.
See [ROADMAP.md](ROADMAP.md) for open-core and future Pro/Team boundaries.

## Privacy Notes

Current local behavior:

- No cloud sync.
- No telemetry.
- No automatic upload of code, documents, or memory.
- Update checks download only public manifest metadata.
- Qdrant support is optional and local.
- Export remains available in the open-source core.

## Development

Run tests:

```bash
python -m pytest
```

Run e2e tests:

```bash
python -m pytest -m e2e
```

Compile-check sources:

```bash
python -m compileall -q src
```

## License

Apache-2.0

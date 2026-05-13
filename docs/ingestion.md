# Source Manifest Ingestion

Truenex Memory can ingest local project documents and agent session logs from a
JSON source manifest.

This is a lower-level ingestion primitive. The normal user workflow should be
agent discovery and global refresh, described in
[agent-discovery-bootstrap.md](agent-discovery-bootstrap.md). Manifests remain
useful for tests, automation, advanced imports, and internally generated source
catalog refreshes.

The ingestion pipeline is local-first:

- It reads files from paths declared by the user.
- It writes only to the configured local `.truenex-memory/` store.
- `--dry-run` reports what would be indexed without creating or modifying the
  database.
- It does not upload source content or memory data.

## Manifest Format

```json
{
  "manifest_version": "1",
  "project": "example-project",
  "sources": [
    {
      "source_type": "project_docs",
      "source_path": "docs",
      "source_tool": "markdown",
      "privacy_scope": "local_private",
      "description": "Project documentation"
    },
    {
      "source_type": "agent_session",
      "source_path": ".codex/sessions",
      "source_tool": "codex",
      "privacy_scope": "local_private",
      "description": "Agent session JSONL logs"
    }
  ]
}
```

Supported fields:

- `manifest_version`: currently `1`.
- `project`: logical project or corpus name stored in indexed metadata.
- `sources`: non-empty list of source declarations.
- `source_type`: one of the supported source types below.
- `source_path`: file or directory path. Relative paths resolve from the
  manifest directory first, then from `--project-root`.
- `source_tool`: optional label such as `codex`, `claude-code`, or `markdown`.
- `privacy_scope`: `local_private` or `project_shared`.
- `description`: optional human-readable note.

## Source Types

Indexable now:

- `project_docs`: text files such as Markdown, text, Python, TOML, YAML, JSON,
  RST, INI, and CFG.
- `agent_session`: Codex/Claude-style JSONL session logs. The parser extracts a
  digest with user requests, assistant text responses, model/session metadata,
  and compaction summaries. Tool calls, tool results, system messages, and
  developer instructions are excluded from the digest.

Reserved for later parsers:

- `agent_memory`
- `operations_note`
- `binary_document`

## CLI

Dry-run:

```bash
truenex-mem ingest manifest --manifest memory-sources.json --dry-run --json
```

Ingest into the current project root:

```bash
truenex-mem ingest manifest --manifest memory-sources.json
```

Ingest into an explicit local store:

```bash
truenex-mem ingest manifest --manifest memory-sources.json --project-root path/to/store-root
```

The report has four sections:

- `index_now`: records parsed and ready for indexing.
- `parse_later`: known source types reserved for future parser support.
- `skipped`: unsupported source types.
- `errors`: manifest, parser, or indexing errors.

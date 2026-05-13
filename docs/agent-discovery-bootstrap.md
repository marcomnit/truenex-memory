# Agent Discovery And Global Bootstrap

This document amends the v1 plan before Phase 3. It keeps the product aligned
with the primary goal: a new Codex, Claude, Cursor, or similar agent session
must recover real project context without the user repeating project history,
paths, SSH notes, database notes, and current state by hand.

Detailed implementation decisions for this phase are tracked in
[phase-2-5-design-decisions.md](phase-2-5-design-decisions.md).

## Product Rule

Truenex Memory starts from the user's agent work history, not from a blind full
disk scan.

The first discovery roots are local agent clients:

- Codex sessions and memories.
- Claude projects, commands, and memories.
- Cursor/Windsurf equivalents when supported.

Those roots are used to discover:

- local project paths;
- project documentation and diaries;
- agent session history;
- SSH aliases and server references;
- remote project roots mentioned in sessions or notes;
- database/service references mentioned in sessions or notes;
- operational documents that were created or cited during agent work.

The tool may propose discovered projects, paths, and servers, but it must not
silently register or scan unrelated areas. User confirmation creates the stable
source catalog.

Discovery reports are ranked by conservative confidence signals such as repeated
evidence across agent roots, existing local paths, and server naming patterns.
Human output is concise by default and shows the highest-confidence candidates
first. JSON output keeps the full candidate list unless the user explicitly
passes a limit.

## User Model

The normal user should not manage manifests, hashes, chunks, or parser details.

The normal flow is:

```bash
truenex-mem global discover --from-agents
truenex-mem global sources review
truenex-mem global sources confirm
truenex-mem global refresh
truenex-mem global status
```

Global initialization should be implicit where possible. If an explicit init
command remains necessary, it is a setup detail and not the main user model.

`global sources review` shows candidate source catalog entries without writing
state. `global sources confirm` writes the confirmed local-private catalog to
`%USERPROFILE%\.truenex-memory\sources.json` by default, or to an explicit
`--catalog` path during validation.

Then, in a new agent session:

```text
Use Truenex Memory. Project example-engine: tell me the current state and next task.
```

or:

```text
Use Truenex Memory. Resolve example-core Postgres and count the users with a read-only query.
```

## Global Store

The global store is the program's internal state, not the place where all user
documents must be copied.

Default location:

```text
%USERPROFILE%\.truenex-memory\
```

Suggested internal layout:

```text
.truenex-memory/
  config.json
  sources.json
  truenex_memory.db
  reports/
    last-discovery.md
    last-refresh.md
  cache/
    session-digests/
```

Original documents remain in their source locations:

```text
project-root/docs/
project-root/README.md
%USERPROFILE%\.codex\sessions\
%USERPROFILE%\.codex\memories\
%USERPROFILE%\.claude\projects\
%USERPROFILE%\.claude\commands\
remote paths referenced through SSH profiles
```

Truenex Memory stores provenance, hashes, chunks, metadata, and generated
digests. It does not require users to reorganize their projects.

## Source Catalog

Discovery creates a proposed source catalog. The user confirms it before it is
used for regular refresh.

Example:

```json
{
  "projects": [
    {
      "name": "example-engine",
      "root": "%USERPROFILE%\\Projects\\example-engine",
      "discovered_from": ["codex-session", "claude-memory"],
      "status": "confirmed"
    },
    {
      "name": "local-agent",
      "root": "D:\\Projects\\local-agent",
      "discovered_from": ["codex-session"],
      "status": "confirmed"
    }
  ],
  "agent_clients": {
    "codex": {
      "sessions": "%USERPROFILE%\\.codex\\sessions",
      "memories": "%USERPROFILE%\\.codex\\memories",
      "status": "confirmed"
    },
    "claude": {
      "projects": "%USERPROFILE%\\.claude\\projects",
      "commands": "%USERPROFILE%\\.claude\\commands",
      "status": "confirmed"
    }
  },
  "servers": [
    {
      "alias": "example-core",
      "source": "agent-history",
      "status": "confirmed"
    }
  ]
}
```

## Refresh Rules

After the first confirmed discovery, refresh must be incremental and
explainable.

For each source file, the ledger stores at least:

- source id;
- original path;
- project name when known;
- source type;
- last modified timestamp;
- content hash;
- last indexed timestamp;
- parser version;
- status: `active`, `missing`, `skipped`, `error`, or `pending`.

Refresh behavior:

- unchanged path and unchanged hash: skip;
- new path: parse and index;
- same path with changed hash: re-parse and replace indexed chunks;
- previously indexed path now missing: mark missing and exclude from default
  retrieval;
- active agent session JSONL modified recently: skip until stable;
- parse failure: keep previous good version active and report the error.

The user-facing report must be simple:

```text
Refresh completed

Projects:
- example-engine: 0 new, 4 modified, 169 unchanged, 0 errors
- sample-agent: 2 new, 0 modified, 71 unchanged, 0 errors

Agent clients:
- Codex sessions: 1 new, 26 unchanged
- Claude sessions: 0 new, 11 unchanged

Missing sources: 0
Errors: 0
```

## Agent Session Parsing

Agent sessions are not indexed as raw dump text by default.

The parser creates a digest containing:

- user requests;
- assistant final text responses;
- compaction summaries;
- session id;
- model when available;
- timestamps;
- current working directory when available;
- project/path/server hints.

The digest excludes:

- system messages;
- developer instructions;
- tool call payloads;
- tool results unless a future parser extracts a specific useful fact.

## Operational Profiles

Operational profiles are derived from confirmed sources and local notes. They
allow agents to resolve project/server/database context before acting.

Examples:

```text
project: example-engine
local_root: ...
servers: example-core, example-engine-host
docs: ...
last_state_sources: ...
```

```text
server: example-core
ssh_alias: example-core
remote_roots: /opt/example-app
services: postgres, qdrant, redis
safe_actions: read-only inspection by default
```

Database actions are not guessed from thin air. They are allowed only when the
memory contains enough confirmed operational context, such as SSH alias,
container/service name, database name, and documented read-only command pattern.

## Required Phase

Add a mandatory phase before Auto Memory:

```text
Phase 2.5 - Agent Discovery And Global Bootstrap
```

Deliverables:

- global store initialization;
- agent client discovery for Codex and Claude;
- proposed project/source/server map;
- user confirmation of discovered sources;
- source catalog;
- ingestion ledger;
- incremental global refresh;
- global status report;
- project context command;
- MCP tools for global/project memory search;
- restart/bootstrap instructions for Codex and Claude.

Quality gates:

- from a clean new session, the agent can ask Truenex Memory for the current
  state of a confirmed project;
- discovery finds known projects from Codex/Claude history without scanning the
  whole disk;
- refresh only reindexes new or changed files;
- deleted files are not used by default retrieval;
- agent session JSONL files are summarized into useful digests;
- a server/database request resolves only from confirmed local memory and cites
  the sources used;
- the user can inspect `global status` and understand what is indexed.

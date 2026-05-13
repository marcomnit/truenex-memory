# Phase 2.5 Design Decisions

This document turns the review notes from `docs/claude_suggest.md` into the
official decisions that guide Phase 2.5. The goal is to avoid drifting into
parallel implementation paths.

## Scope

Phase 2.5 exists to make Truenex Memory useful at the start of a new agent
session.

The user should be able to ask:

```text
Use Truenex Memory. Project example-engine: tell me the current state and next task.
```

or:

```text
Use Truenex Memory. Resolve example-core Postgres and count users with a read-only query.
```

The system must resolve that context from confirmed local memory, not from a
fresh manual explanation by the user.

## User Experience Decision

The normal user workflow is not manifest-driven.

Normal flow:

```bash
truenex-mem global discover --from-agents
truenex-mem global sources review
truenex-mem global sources confirm
truenex-mem global refresh
truenex-mem global status
```

Manifests remain a lower-level primitive for tests, automation, and advanced
imports.

## Command Tiers

The CLI must be documented by usage tier so the product stays simple.

Tier 0: daily use:

- `search`
- `add`
- `list`
- `status set`

Tier 1: project/global maintenance:

- `index`
- `ingest manifest`
- `global discover`
- `global refresh`
- `global status`
- `logs`
- `trace show`
- `export`

Tier 2: administration:

- `doctor`
- `migrate status`
- `migrate apply`
- `migrate backup-list`
- `migrate restore`
- `import`

Tier 3: integration:

- `mcp`
- `adapter ...`
- `update check`
- `version`
- `version-info`

README quickstart should stay focused on Tier 0 and the minimum global flow.

## Global Store Topology

Decision for Phase 2.5: use a centralized global store first.

Default location:

```text
%USERPROFILE%\.truenex-memory\
```

The global DB contains:

- source catalog;
- source ledger;
- discovered/confirmed project profiles;
- discovered/confirmed server profiles;
- indexed chunks for global search;
- retrieval logs for global agent bootstrap.

Project-local `.truenex-memory` stores remain supported for current behavior,
but global bootstrap uses the global store.

Reason: centralized search and simpler agent bootstrap matter more than optimal
large-scale storage in the first usable version.

## Source Catalog

Discovery produces candidates. The source catalog contains only confirmed
sources.

Candidate source types:

- agent client roots: Codex, Claude, later Cursor/Windsurf;
- local project roots;
- remote project roots referenced through confirmed SSH profiles;
- operational notes and documents;
- server aliases;
- database/service hints.

Confirmed entries must record:

- stable id;
- source type;
- path or alias;
- project name when known;
- discovered_from;
- confirmation status;
- privacy scope.

## Source Ledger

Incremental refresh requires a ledger before any automatic refresh work.

Minimum fields:

- source id;
- source path or alias;
- project name;
- source type;
- parser version;
- content hash;
- last modified timestamp;
- last indexed timestamp;
- status: `active`, `pending`, `error`, `missing`, `skipped`;
- error message;
- chunk count.

Refresh rules:

- unchanged path and hash: skip;
- new source: parse and index;
- changed hash: re-parse and replace active chunks;
- missing file: mark missing and exclude from default retrieval;
- parse error: keep previous good version active and report error;
- active JSONL session: skip until stable.

Initial stability threshold for JSONL sessions: no writes for at least 120
seconds.

## Discovery Algorithm

Discovery must not scan the whole disk.

Allowed first-pass roots:

- `%USERPROFILE%\.codex\sessions`
- `%USERPROFILE%\.codex\history.jsonl`
- `%USERPROFILE%\.codex\memories`
- `%USERPROFILE%\.claude\projects`
- `%USERPROFILE%\.claude\history.jsonl`
- `%USERPROFILE%\.claude\commands`

Discovery extracts candidates from agent work history:

- project paths;
- document references;
- SSH aliases and hosts;
- remote roots;
- database/service hints.

Discovery must be conservative. It is better to show fewer high-confidence
candidates than a large noisy list.

## Operational Profiles

Operational profiles are core, not Pro-only.

Profiles are generated from confirmed sources and are used by agents to resolve
actions before execution.

Minimum project profile:

- project name;
- local roots;
- remote roots;
- related servers;
- important docs;
- recent session sources;
- current status sources.

Minimum server profile:

- alias;
- host/user when known;
- remote roots;
- services;
- safe read-only commands when documented.

Minimum database profile:

- related server;
- service/container;
- database name;
- user name when documented;
- read-only inspection command when documented;
- source citations.

Agents may execute operational actions only when the profile is confirmed and
the action can cite local sources. Default operational actions are read-only.

## Privacy Boundary

Local memory is allowed to contain private operational knowledge.

The public repository must not contain private project details, credentials,
server secrets, or user-specific source catalogs.

Phase 2.5 must distinguish:

- public docs and code in the repository;
- private local global store;
- private reports under the user's memory directory.

Agent sessions should not be indexed as raw dumps by default. Digest parsers
must exclude:

- system instructions;
- developer instructions;
- raw tool payloads;
- tool results, unless later converted into explicit useful facts.

Credential scrubbing is a future hardening task. Until then, all global memory
content remains local-private by default.

## Immediate Implementation Order

Do not jump to refresh or MCP bootstrap before these are done.

1. Improve discovery ranking/filtering so candidate maps are concise.
2. Add source catalog domain model.
3. Add review/confirm commands for discovered candidates.
4. Add source ledger schema and migration.
5. Add incremental refresh using catalog + ledger.
6. Add global status report.
7. Add project context command.
8. Add MCP/global bootstrap tools.

## Deferred

These are valid but should not interrupt Phase 2.5A/B/C:

- rich retrieval diagnostics;
- expanded doctor;
- selective export;
- advanced parser version migration policy;
- load testing for very large corpora;
- full FAQ and mental model documentation.

They become important after global discovery, catalog, ledger, and refresh work.


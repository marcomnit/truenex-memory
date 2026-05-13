# Phase 3 Auto Memory Design Decisions

This document locks the Phase 3 design before implementation. It builds on the
completed Phase 2.5 global discovery/bootstrap work and keeps Auto Memory
aligned with the original product goal: a coding agent should recover useful
project context with minimal user repetition, while every automatic step remains
local, explainable, incremental, and reversible.

## Scope

Phase 3 introduces automatic memory maintenance, not an always-on autonomous
agent.

The first usable workflow is manual and explicit:

```bash
truenex-mem global auto run
truenex-mem global auto status
```

The command may perform discovery refresh, source refresh, and conservative
auto-memory generation, but only against confirmed local sources. It must not
scan the whole disk, execute SSH/database actions, upload content, or mutate
source files.

Background watchers, scheduled daemons, and UI review flows are deferred until
the manual workflow proves useful on real projects.

Any future scheduler must run only conservative indexing refresh by default,
not generated-memory creation. In particular, a background job must not run
`global auto run --auto-memory`; generated auto memories remain an explicit
reviewable workflow until quality, deduplication, runtime, concurrency, and log
rotation are proven on real data.

Phase 3A does not expand the MCP tool surface. Agents keep using the Phase 2.5
read-only global MCP tools after the user runs the local auto command.

## Relationship To Phase 2.5

Phase 2.5 already provides the foundation:

- confirmed source catalog;
- source ledger;
- incremental global refresh;
- global status;
- project context;
- read-only MCP bootstrap tools.

Phase 3 must reuse those primitives. It must not create a parallel ingestion
path.

Auto Memory is an orchestration layer around the existing catalog, ledger,
refresh, repository, and retrieval layers.

## User Model

Daily use should stay simple:

```bash
truenex-mem global auto run
```

Expected behavior:

1. Load the confirmed global source catalog.
2. Detect new, modified, unchanged, missing, skipped, and error sources.
3. Reindex only new or changed content.
4. Keep recently modified agent session logs stable by skipping them.
5. Optionally create conservative unverified memory nodes from high-signal
   indexed content.
6. Print a concise report with counts and warnings.

The user should be able to run it repeatedly without duplicates, uncontrolled
noise, or surprise side effects.

When real validation shows that a single high-value source is missing from the
confirmed catalog, the user can add that source explicitly without replacing the
catalog:

```bash
truenex-mem global sources add \
  --source-type document \
  --path-or-alias %USERPROFILE%\.codex\memories\truenex-memory.md \
  --discovered-from codex-memories \
  --yes
```

This command updates only the source catalog. It does not touch the SQLite DB;
refresh/auto-run remains a separate explicit step.

## Command Contract

### `global auto run`

Proposed options:

```bash
truenex-mem global auto run \
  --home %USERPROFILE% \
  --catalog %USERPROFILE%\.truenex-memory\sources.json \
  --db %USERPROFILE%\.truenex-memory\truenex_memory.db \
  --stability-seconds 120 \
  --skip-refresh \
  --auto-memory \
  --dry-run \
  --json
```

Initial implementation may internally call the existing global refresh engine,
but the command name establishes the daily workflow for Phase 3.

`--skip-refresh` is an explicit fast path for already-indexed stores. It must be
used with `--auto-memory`, does not require the source catalog, and must not
parse catalog sources or source files. It exists for quick local review or
generation from the current DB when the ledger/index is already current. The
default command still performs normal refresh first.

Report fields:

- catalog entries;
- new records;
- modified records;
- unchanged records;
- skipped records;
- unstable session records;
- missing records;
- parse/index errors;
- indexed records;
- whether source refresh was explicitly skipped;
- auto-memory candidates;
- auto-memory created;
- auto-memory duplicates skipped;
- auto-memory active duplicates skipped;
- auto-memory unverified duplicates skipped;
- auto-memory rejected/tombstoned duplicates skipped;
- low-confidence candidates skipped;
- candidates skipped by the per-run auto-memory limit;
- candidates skipped by the per-source auto-memory limit;
- non-document chunks skipped before auto-memory creation.
- noisy agent-session chunks skipped before auto-memory creation.

### `global auto status`

Read-only summary for the automatic layer:

- last auto run timestamp;
- confirmed sources;
- active ledger rows;
- missing/error/skipped ledger rows;
- unstable session count;
- transient unstable session count within the stability window;
- stale unstable session count that still requires a refresh or review;
- actionable skipped count, excluding expected skips and transient session
  writes;
- skipped reason breakdown;
- unverified memory count;
- current auto-memory candidate count;
- current duplicate skip count, including active, unverified, and rejected
  duplicate breakdown;
- current low-confidence, non-document, and noisy-session skip counts;
- warnings that require user review.

This can initially be a thin wrapper around `global status` plus auto-specific
counts.

### `global auto review`

Read-only inspection of generated unverified auto-memory nodes:

- total generated unverified auto memories;
- source-path distribution;
- id, title, type, status, confidence, source path, and content excerpt;
- JSON output with full local content for local review workflows;
- optional source-path substring filter and display limit.

This command does not approve, reject, promote, demote, or mutate generated
memory nodes. It exists so the user can inspect Phase 3.5 output without raw SQL
or direct database access.

### `global auto approve`

Explicitly promotes one generated auto memory:

```bash
truenex-mem global auto approve <memory-id>
```

Safety rules:

- only `status='unverified'`, `source_kind='auto'`, `created_by='auto'` rows are
  eligible;
- the SQL update must include the same guard atomically, not only a command-layer
  pre-check;
- manual memory and non-unverified auto memory are never promoted by this
  command;
- the command changes only `status` and `updated_at`.

### `global auto reject`

Explicitly rejects one generated auto memory:

```bash
truenex-mem global auto reject <memory-id>
```

Safety rules:

- only `status='unverified'`, `source_kind='auto'`, `created_by='auto'` rows are
  eligible;
- rejected rows become `obsolete`, preserving local content, source path,
  content hash, and provenance;
- auto-generation treats an exact `obsolete` content hash as a tombstone, so a
  rejected memory is not recreated as a new unverified node on the next run.

### `global auto prune`

Compacts rejected auto memories after review:

```bash
truenex-mem global auto prune [--source README] [--limit 100] [--yes]
```

Safety rules:

- dry-run by default; `--yes` is required to write;
- eligible rows are limited to `status='obsolete'`, `source_kind='auto'`,
  `created_by='auto'`, and non-null `content_hash`;
- active, unverified, conflicting, superseded, and manual nodes are not touched;
- prune does not hard-delete rows in Phase 3. It replaces large rejected content
  with a compact tombstone while keeping `content_hash` and source provenance so
  exact rejected content remains suppressed.

### `global auto promote`

Creates one curated active memory from a noisy generated auto memory:

```bash
truenex-mem global auto promote <memory-id> \
  --title "Curated title" \
  --content "Clean, compact memory text." \
  --type decision
```

This exists for the real-data case where a generated `unverified` memory
contains a useful fact inside raw session text, quotes, terminal output, or
other noise. In that case `approve` would pollute retrieval and `reject` would
lose the useful distilled fact.

Safety rules:

- only `status='unverified'`, `source_kind='auto'`, `created_by='auto'` rows are
  eligible;
- the curated replacement is inserted as `status='active'`,
  `source_kind='curated_auto'`, `created_by='curated_auto'`;
- the original generated row becomes `obsolete`;
- both writes happen in one SQLite transaction;
- the curated row copies source document/chunk/path provenance from the
  original row, so later tombstoning of the obsolete generated row does not
  break auditability;
- empty title/content are refused;
- duplicate active curated content is refused by exact content hash;
- `--dry-run` validates and shows the planned replacement without writing;
- documents, chunks, source files, and the source ledger are not modified.

### `global search`

Read-only keyword search over the global store:

- searches generated memory nodes and indexed source chunks from the global DB;
- supports `--kind all|memory|chunks` so review searches can focus on generated
  memory without mixing in indexed source chunks;
- includes `active` and `unverified` memory by default, with status visible;
- excludes inactive memory unless requested explicitly;
- respects missing/skipped ledger rows for indexed chunks;
- does not write retrieval logs or mutate the DB.

This command is the CLI validation path for checking whether the global store is
actually useful to a new agent session after auto-run.

### Deferred Commands

These are valid but not part of Phase 3A:

```bash
truenex-mem global auto watch
```

Background watch/daemon behavior remains deferred until the manual lifecycle is
proven on real local stores.

## Ledger State Machine

The source ledger remains the authority for incremental behavior.

Allowed states already exist:

- `pending`;
- `active`;
- `skipped`;
- `missing`;
- `error`.

Phase 3 locks these transitions:

| From | To | Meaning |
|---|---|---|
| none | active | New source parsed and indexed successfully. |
| none | skipped | New source is not indexable or not stable yet. |
| none | missing | Catalog/source points to a path that does not exist. |
| none | error | Parser or indexer failed before a good version existed. |
| active | active | Existing source changed and was reindexed successfully. |
| active | skipped | Existing agent session changed recently; previous active version remains usable. |
| active | missing | Source disappeared; previous indexed data is excluded from default retrieval when possible. |
| active | error | Reparse failed; previous good indexed data remains available until a safer invalidation policy exists. |
| skipped | active | Previously skipped source became indexable or stable. |
| skipped | missing | Previously skipped source disappeared. |
| skipped | error | Previously skipped source now fails unexpectedly. |
| missing | active | Missing source reappeared and was indexed. |
| error | active | Failed source retried successfully. |
| error | missing | Failed source disappeared. |

`pending` is reserved for queued work and migration compatibility. Phase 3A
does not need to write `pending` during normal auto-run; if later used, every
pending row must either become `active`, `skipped`, `missing`, or `error` within
the same reportable operation.

Important rule: a failed reindex must not destroy the last good usable version.
The report must clearly show the error so the user can fix the source or parser.

## Chunk Replacement Policy

The current repository replaces all chunks for a document when that document is
reindexed. Phase 3 keeps that policy:

```text
same document id + changed content -> delete old chunks -> insert new chunks
```

This avoids orphaned chunks after a document shrinks or headings change.

For missing files, Phase 3 does not hard-delete old rows. It marks the ledger
entry as `missing` and reports the state. Hard pruning is deferred because data
retention and provenance matter more than reclaiming space in the first Auto
Memory version.

Blocking Phase 3.1 gate: before `global auto run` is considered usable beyond
validation, verify whether current retrieval can exclude chunks whose ledger
record is `missing` or non-indexed `skipped`. If it cannot, Phase 3.1 must add
a ledger-aware retrieval filter or block broad use with an explicit diagnostic.
Skipped JSONL rows that already had a previous active version keep that previous
version available; skipped JSONL rows with no previous active version must not
produce retrievable chunks.

## Source Stability

Agent session files are append-heavy and can be incomplete while an agent is
running. Auto Memory must treat them differently from normal project documents.

Initial rule:

```text
JSONL agent session file is stable only if mtime age >= 120 seconds.
```

The value must be configurable through command options first. Persistent config
can come later.

When a new JSONL file is unstable:

- do not index it;
- write or keep a skipped ledger row;
- include the reason in the report.

When an already active JSONL file becomes unstable:

- do not overwrite the active version;
- report it as skipped/unstable;
- keep the previous active version available.

No background watcher is allowed in Phase 3A, because explicit manual runs are
easier to reason about and debug.

## Agent Session Digest Policy

Agent sessions must not be indexed as raw dumps by default.

Digest content may include:

- user requests;
- assistant final text responses;
- compaction summaries;
- session id when available;
- model name when available;
- current working directory when available;
- project/path/server hints.

Digest content must exclude:

- system instructions;
- developer instructions;
- raw tool call payloads;
- raw tool results;

Phase 3A does not implement credential scrubbing. The safety boundary is:

- tool payloads and tool results are excluded by parser policy;
- all session-derived memory remains local-private;
- real-data validation must avoid intentionally indexing known credential dumps;
- suspected credential-heavy sources should be excluded from the confirmed
  catalog until a redaction parser exists.

Phase 3A can reuse the current agent session parser, but must add tests that
prove auto-run preserves the same digest safety properties.

## Auto-Generated Memory Nodes

Phase 3 distinguishes between indexed source chunks and generated memory nodes.

Indexed chunks are source-grounded retrieval material.

Auto-generated memory nodes are derived summaries/facts. They are useful, but
they carry more risk because they can create duplicate or noisy knowledge.

Decision:

1. Phase 3A may ship `global auto run` as automatic refresh only.
2. Auto-generated memory nodes enter as Phase 3B behind conservative rules.
3. Every generated node must use status `unverified`.
4. Promotion from `unverified` to `active` is manual only.
5. Automatic generation must never overwrite manual memory.

Default generated node metadata:

- `status = "unverified"`;
- `created_by = "auto"`;
- `source_kind = "auto"`;
- `source_document_id` and/or `source_chunk_id` when known;
- `source_path` always set when available;
- `confidence` set by deterministic rules.

## Deduplication And Noise Control

Before creating an auto-generated memory node:

1. Compute the content hash of the candidate memory text.
2. Check for an existing memory node with the same content hash in the same
   project.
3. If an `active` node exists, skip the auto candidate and report a duplicate.
4. If an `unverified` node exists, update metadata/confidence only if this does
   not destroy provenance; otherwise skip and report a duplicate.
5. If no node exists and confidence is above the creation threshold, create the
   unverified node.

No semantic deduplication in Phase 3. Exact content hash is enough for the first
safe version.

Initial confidence defaults:

- project documentation derived candidate: `0.80`;
- agent session digest derived candidate: `0.60`;
- ambiguous note candidate: `0.50`;
- creation threshold: `0.50`;
- retrieval threshold for unverified memory: `0.50`.

If this creates too much noise on real data, the threshold should be raised
before adding richer classifiers.

Initial real-data noise metric for Phase 3.5:

- if more than 40% of generated candidates are duplicates, low-confidence
  drops, or user-rejected facts, raise thresholds or narrow generation rules;
- if any generated `decision` is wrong in manual review, demote the classifier
  rule to `note` until a stricter signal exists.
- raw agent-session transcripts, resume wrappers, pure "all user messages"
  inventories, and command-only snippets must remain source chunks only; they
  should not become generated memory nodes.

## Classification Policy

No ML classifier in Phase 3A.

If Phase 3B adds classification, it must be deterministic and conservative:

- clear decision wording -> `decision`;
- implementation note -> `note`;
- error/fix history -> `issue`;
- repeated implementation approach -> `pattern`;
- unclear content -> `note`.

Classifier mistakes are product-risky, so ambiguous content must never be
upgraded into a `decision`.

## Retrieval Policy

Unverified memory may appear in default retrieval only if it is clearly marked
as unverified and meets the confidence threshold.

CLI/MCP outputs must preserve:

- source path;
- memory type;
- status;
- score;
- confidence when available.

Agents must be able to tell whether a result is a confirmed memory or an
automatic unverified candidate.

## Configuration

Phase 3A starts with command options instead of a new config system:

- `--stability-seconds`, default `120`;
- `--auto-memory`, default off if generated memory nodes are not ready;
- `--min-confidence`, default `0.50` once auto nodes exist;
- `--auto-memory-limit`, default `50`, with `0` meaning unlimited;
- `--auto-memory-per-source-limit`, default `5`, with `0` meaning unlimited;
- `--dry-run`;
- `--json`.

Persistent config can be introduced later only if repeated real use proves it
is needed.

`--dry-run` is fully read-only. It must not mutate source files, the SQLite
database, the source ledger, vector stores, reports, or config files.

## Tests And Quality Gates

Phase 3A must pass automatic gates:

- `global auto run --dry-run` does not mutate DB, ledger, or source files;
- new files are detected and indexed;
- modified files are reindexed and old chunks are replaced;
- unchanged files are skipped;
- deleted files become `missing`;
- unstable JSONL sessions are skipped;
- active JSONL sessions keep their previous active version when a new write is
  unstable;
- parse/index errors do not destroy the previous good version;
- `global auto status` reports auto-specific counts;
- no cloud/network/SSH/database action is executed.
- retrieval does not return chunks from missing ledger rows or skipped rows that
  never had an active indexed version.

Phase 3B auto-generated memory gates:

- generated memory nodes are `unverified`;
- manual memory is never overwritten;
- exact duplicate candidates are skipped;
- low-confidence candidates are skipped;
- unverified retrieval output is clearly marked.

Manual real-data gate:

1. Run auto dry-run on the real global store.
2. Run auto run on the real global store.
3. Modify five real project documents.
4. Run auto run again.
5. Confirm search/project context improves or stays stable.
6. Confirm report noise is understandable and not excessive.

## Implementation Sequence

### Phase 3.0: Design Lock

This document.

### Phase 3.1: Manual Auto Run Wrapper

Add `global auto run` as a daily-use command over existing global refresh.

Keep scope narrow:

- no generated memory nodes yet;
- no watcher;
- no persistent config;
- no new parser migration policy.

First implementation checkpoint: verify and, if needed, enforce the missing and
skipped retrieval policy before calling Phase 3.1 complete.

### Phase 3.2: Auto Status

Add `global auto status` with read-only summary of auto-run readiness and
problem states.

### Phase 3.3: Ledger Transition Hardening

Codify and test the state machine. Confirm that parse/index errors preserve the
last good indexed version.

### Phase 3.4: Optional Unverified Auto Memory

Only after 3.1-3.3 are stable, add conservative generated memory nodes with
exact-hash deduplication and confidence thresholds.

### Phase 3.5: Real-Data Validation

Run the full workflow on the user's local global store, inspect reports,
searches, and MCP outputs, and tune thresholds before any daemon/watch mode.

## Non-Goals

Phase 3 does not implement:

- background daemon or watcher;
- cloud sync;
- UI review;
- licensing;
- SSH/database execution;
- credential discovery or credential scrubbing;
- semantic/ML deduplication;
- automatic promotion to `active`;
- automatic deletion/pruning of missing data;
- parser version migration;
- cross-project merge decisions.

These may be valid later, but adding them now would make the automatic layer too
hard to reason about.

## Acceptance Criteria

Phase 3 is complete when:

- daily manual auto-run works on confirmed local sources;
- reports are understandable;
- repeated runs are idempotent;
- modified/deleted/unstable/error sources behave according to the state machine;
- generated memory, if enabled, is conservative and marked unverified;
- a new agent session can use MCP/project context after auto-run without the
  user repeating what changed;
- the user can inspect where data came from and why it was or was not indexed.

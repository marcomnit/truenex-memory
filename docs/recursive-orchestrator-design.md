# Recursive Orchestrator Design

This document translates the **RecursiveMAS** pattern (recursive multi-agent
computation in latent space) into the Truenex Memory ecosystem.  Because we
do not have access to the internal hidden states of closed-weight models
(GPT-4, Claude, Kimi, DeepSeek), we replicate the *architectural idea* at the
**orchestration layer**: Truenex Memory itself becomes the shared latent
workspace, and lightweight adapters become the "RecursiveLink" modules.

## Scope

The Recursive Orchestrator is an explicit, local-first workflow engine.  It
does **not** run autonomous background loops.  The user must start a loop
with a config file and can inspect, abort, or rerun it at any time.

The first usable surface is CLI-driven:

```bash
truenex-mem orchestrate run loop.json
```

Where `loop.json` declares a sequence of phases (external commands), a
maximum recursion depth, and a convergence strategy.

## Relationship To Existing Components

The orchestrator reuses existing primitives:

- **MemoryService / MemoryRepository** — persistent shared workspace (the
  "latent space" of the system).
- **TaskStore** — tracks the loop itself as a long-running task with steps.
- **content_hash** — cheap deterministic convergence check.
- **Semantic retrieval** — future work: use embedding similarity instead of
  exact hash for soft convergence.

It does **not** create a parallel persistence path.

## Conceptual Mapping RecursiveMAS → Truenex Memory

| RecursiveMAS concept | Truenex Memory equivalent |
|---|---|
| Latent hidden state | Memory node of type `recursive_round` (JSON payload) |
| Inner Link | Auto-memory lifecycle (refine a thought in-place) |
| Outer Link | Phase adapter: output of agent *N* is written to memory; agent *N+1* reads it via semantic search |
| Recursion depth | Configurable `max_depth` with early exit on convergence |
| Gradient sharing / credit assignment | Human outcome on the TaskStore record + step-level `brain_judgment` |
| Residual connection | The adapter preserves raw output verbatim and only adds metadata / formatting shifts |

## User Model

A loop is defined by a JSON config:

```json
{
  "name": "kimi-owner-loop",
  "phases": [
    {
      "name": "plan",
      "command": "kimi-cli plan --project truenex-ai",
      "role": "architect"
    },
    {
      "name": "generate",
      "command": "aider --model deepseek --msg-file .truenex-memory/loop/plan.md",
      "role": "coder"
    },
    {
      "name": "validate",
      "command": "codex validate --tests",
      "role": "reviewer"
    }
  ],
  "max_depth": 3,
  "convergence_strategy": "hash"
}
```

Expected behaviour:

1. Load the config, validate phase names are unique.
2. Open a TaskStore record for the loop.
3. For each iteration (1 … max_depth):
   a. Run every phase command in order, capturing stdout.
   b. Write a `recursive_round` memory node containing the output, iteration,
      phase, and content hash.
   c. After the last phase, compute an aggregate hash of the iteration.
   d. If the aggregate hash equals the previous iteration’s aggregate hash,
      declare **convergence** and exit early.
4. Close the task record.
5. Print a concise report: iterations executed, convergence status, round IDs.

Commands are responsible for their own side effects (e.g. writing code files).
The orchestrator only guarantees that stdout is captured and persisted.

## Convergence Strategies

| Strategy | Description | When to use |
|---|---|---|
| `hash` | Exact SHA-256 of the concatenated phase outputs. | Deterministic code generators, linters. |
| `last_phase_hash` | Compare only the last phase output across iterations. | When intermediate phases are non-deterministic (timestamps, temp paths) but the final artifact stabilises. |

Future strategies (not in Phase 1):
- `embedding_cosine`: compare iteration outputs via semantic embedding.
- `human_gate`: pause after each iteration and prompt the user for continuation.

## Data Model

### RecursiveRound (stored as memory node `type="recursive_round"`)

```json
{
  "iteration": 2,
  "phase": "validate",
  "output": "...stdout content...",
  "output_hash": "a3f2...",
  "loop_name": "kimi-owner-loop",
  "created_at": "2026-05-20T18:00:00Z"
}
```

The orchestrator also writes a top-level `recursive_summary` memory node at
the end of the loop with aggregated metadata.

## Security & Safety

- Commands run with `shell=True` **only** from an explicit user-supplied config
  file.  No inline command strings are accepted from remote sources.
- The orchestrator never scans the disk or auto-discovers commands.
- Each phase runs in the user’s current working directory; no sandboxing is
  added (the user already trusts the tools they invoke).

## Implementation Log

### 2026-05-21 — Round 2 Hardening (Codex Review)

After initial PoC + Claude Round 1 fixes, Codex Round 2 identified 4 blockers.
All were resolved and verified with 18/18 unit tests passing plus end-to-end
smoke test.

**Fixes applied:**

1. **Deferred GUI import** (`cli/main.py`)
   - Problem: `from truenex_memory.serve import run_serve` at module top-level
     caused `ImportError` / `sys.exit()` for users without `[gui]` extras.
   - Fix: Moved import inside the `serve()` command function. CLI commands
     (`init`, `search`, `orchestrate`, etc.) now work in default installs.

2. **Fresh-project init regression** (`cli/main.py`)
   - Problem: `resolve_project_root()` fell back to `Path.home()` when no
     `.truenex-memory` existed in cwd, causing `init` to initialise the home
     directory instead of the current repo.
   - Fix: Removed home fallback; default is now `"."` (current directory).

3. **Non-zero phase exit codes ignored** (`orchestration/recursive_loop.py`)
   - Problem: `subprocess.run()` returncode was recorded but the loop continued.
     A failing test/validation command could produce stdout that looked like
     convergence, closing the task as successful.
   - Fix: After `_run_phase()`, if `returncode != 0` raise `RuntimeError`.
     Caught by existing `except Exception`, sets `error_msg`, breaks loop,
     and closes the TaskStore record with `human_outcome=-1`.

4. **Config validation outside CLI error handler** (`cli/orchestrate_commands.py`)
   - Problem: `RecursiveLoopConfig.validate()` was called inside `loop.run()`,
     so semantic errors (empty name, duplicate phases, invalid strategy) raised
     raw `ValueError` instead of clean `Error: invalid config file` with exit 1.
   - Fix: Call `config.validate()` immediately after `from_dict()`, inside the
     existing `try/except` block.

**Test additions:**
- `test_non_zero_phase_returncode_stops_loop` — verifies loop halts on phase
  failure and reports error correctly.
- `test_run_command_invalid_config_semantic` — verifies JSON-valid but
  semantically-invalid config produces exit code 1 with clean message.

**Smoke test:**
```bash
truenex-mem orchestrate run examples/recursive-loop-kimi-owner.json
# → converges in 2 iterations, 6 rounds, no errors
```

### 2026-05-20 — Round 1 Hardening (Claude Read-only + Codex CTO)

Initial PoC review identified 4 blockers, all resolved before Round 2:

1. **Exact ID lookup for converge-check** — added `MemoryRepository.get_memory_node()`
   and `MemoryService.get_memory_node()` to replace semantic search when comparing
   two persisted rounds by ID.
2. **Partial iteration counting** — added explicit `iterations_completed` counter
   so mid-loop errors report the correct number of iterations actually finished.
3. **Subprocess timeout** — added `timeout` field to `RecursiveLoopConfig`
   (default 300s) and passed it to `subprocess.run()`.
4. **`ProjectConfig` attribute fix** — used `project_root.name` instead of
   non-existent `project_name`.
5. **Missing validation** — added `convergence_strategy` and `timeout` checks
   to `RecursiveLoopConfig.validate()`.
6. **Integration test gap** — added `test_run_with_real_memory_service` using
   real `MemoryService` + `TaskStore` + mocked subprocess.

## Future Work

1. **Semantic convergence** — use `HashingEmbedder` to compare iteration
   outputs by meaning rather than by exact bytes.
2. **MCP tool exposure** — expose `orchestrate_run` and `orchestrate_status`
   as MCP tools so agents can start loops programmatically.
3. **Parallel phases** — DAG support instead of strict sequential phases.
4. **Checkpoint / resume** — store iteration state on disk so a long loop can
   survive process restarts.

# Contributing

Truenex Memory is Apache-2.0 and welcomes contributions. This document covers
the mechanics for pre-1.0 development.

## Setup

```bash
python -m venv .venv

# Windows:
.venv\Scripts\pip install -e ".[dev]"
# Linux / macOS:
.venv/bin/pip install -e ".[dev]"
```

Requires Python >= 3.12.

## Running Tests

```bash
python -m pytest
```

CI runs the default test suite plus e2e tests. Optional Qdrant tests require a
reachable local Qdrant service:

```bash
python -m pytest -m e2e
python -m pytest -m qdrant
```

## Code Conventions

- `src/` layout with `truenex_memory` package.
- CLI uses Typer; each command group lives in `cli/`.
- Core logic stays in `core/`, stores in `store/`, adapters in `adapters/`.
- No comments for what the code says; only for non-obvious why.
- Keep PRs focused: one concern per branch.

## Before Submitting

1. `python -m pytest` passes.
2. `python -m compileall -q src` succeeds.
3. CLI/MCP changes include focused tests or e2e coverage.
4. New CLI surface is reflected in `--help` (Typer generates it).
5. No secrets, credentials, or machine-specific paths.

## Pull Request Process

1. Open an issue describing the problem before sending a PR for new features.
2. Bug fixes can be sent directly.
3. PRs should target `main`.
4. Keep commit history clean; squash if needed.

## Project Scope

The open-source core stays local-first and agent-focused. Features that require
accounts, subscriptions, or cloud infrastructure belong in separate
distributions. The core must remain useful without any external service.

Do not add SSH/database execution from discovered aliases to the core. Discovered
operational references are context hints unless a future, explicitly reviewed
execution profile is added.

## License

By contributing, you agree that your work is licensed under Apache-2.0.

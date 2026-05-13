# Testing

Truenex Memory has two test gates.

## Default Test Suite

Fast local tests run by default:

```bash
python -m pytest
```

This excludes tests marked `e2e` or `qdrant`.

## End-To-End Gates

Run e2e tests explicitly:

```bash
python -m pytest -m e2e
```

Current e2e coverage:

- clean virtual environment install from the repository;
- CLI quickstart: help, doctor, init, add, index, search, export;
- MCP stdio JSON-RPC session: initialize, tools/list, memory_add,
  memory_search, and read-only global bootstrap tools.

The e2e tests are local-only. They do not contact GitHub, Qdrant, cloud sync,
telemetry, payment systems, or licensing services.

## Optional Qdrant Integration

Qdrant integration tests are opt-in:

```bash
python -m pip install -e ".[qdrant]"
docker compose up -d qdrant
python -m pytest -m qdrant
```

These tests require `qdrant-client` and a reachable local Qdrant service. They
are skipped when Qdrant is not available.

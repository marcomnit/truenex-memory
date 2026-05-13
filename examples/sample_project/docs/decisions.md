# Decisions

## ADR-001: Use SQLite For Local Metadata

Status: active

We use SQLite for local metadata because it is local-first, easy to back up, and
does not require a server for personal projects.

## ADR-002: Plan Qdrant For Vectors

Status: active

Qdrant is the planned vector database for vector search. The first local MVP can
fall back to deterministic local embeddings and SQLite persistence when Qdrant
is unavailable.

## ADR-003: Use MCP stdio For Agents

Status: active

The MCP server uses stdio so local coding agents can launch Truenex Memory as a
subprocess and exchange newline-delimited JSON-RPC messages.

## ADR-004: No Automatic Uploads

Status: active

Truenex Memory must not upload code, memory, or project documents automatically.
The local MVP keeps cloud sync and telemetry disabled.

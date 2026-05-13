# Architecture

## Local Storage

Truenex Memory stores local metadata in SQLite. Documents, chunks, memory nodes,
retrieval logs, provenance, and schema state belong in the local SQLite database.

## Vector Search

Qdrant is the planned vector store for semantic retrieval when a local or
self-hosted vector service is available. The core must keep working with a local
fallback when Qdrant is not running.

## Agent Interface

MCP v1 uses stdio. Coding agents should call `memory_search` before making
project claims and can call `memory_add` to store local notes or decisions.

## Privacy Boundary

Code, memory, and project documents are not uploaded automatically. Cloud sync,
team memory, and telemetry are out of scope for the local MVP unless the user
explicitly opts in later.

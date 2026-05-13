# Security Policy

## Principles

Truenex Memory is local-first: no code, memory, or project data leaves your
machine unless you explicitly export or copy it. There is no cloud sync,
telemetry, or automatic upload.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

Pre-1.0 releases receive security patches on a best-effort basis. APIs and
command output may still change before 1.0.

## Reporting a Vulnerability

To report a security issue, open a GitHub issue with a minimal reproduction.
Avoid attaching sensitive files. We will acknowledge within 5 business days.

For sensitive reports that should not be public, use a private maintainer
channel until GitHub private vulnerability reporting is enabled for the
repository.

## Local-First Security Model

- **No network by default.** The MCP transport is local stdio. Qdrant is
  optional and bound to localhost when configured.
- **No telemetry.** `truenex-mem doctor --privacy` confirms this at any time.
- **No automatic upload.** Source ingestion reads local files only.
- **Embedding fallback.** When Qdrant is unavailable, a deterministic local
  fallback is used for tests and offline operation.
- **Database is local SQLite.** Project-local data is stored in
  `.truenex-memory/`. Global memory uses the configured local global store.
  No external database is required.

## What to Expect

- We do not ship binaries that phone home.
- We do not bundle tracking or analytics.
- We do not require accounts, tokens, or registration.
- Export (`truenex-mem export`) produces a readable JSON file you fully own.

## Dependency Hygiene

Dependencies are intentionally small. Run `pip-audit` or equivalent before
deploying in sensitive environments.

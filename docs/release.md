# Release And Update Plan

This document tracks the release/update scaffold for Truenex Memory.

## Version Values

The application keeps these versions separate:

- `app_version`
- `db_schema_version`
- `mcp_tools_version`
- `license_format_version`
- `memory_export_version`
- `cloud_api_version`

See `src/truenex_memory/release/version.py`.

## Manifest Repository

`marcomnit/truenex-memory-releases` is the public metadata repository. It hosts
`version.json` on the default branch.

Raw URL:

```text
https://raw.githubusercontent.com/marcomnit/truenex-memory-releases/main/version.json
```

Initial manifest:

```json
{
  "manifest_version": "1",
  "version": "0.1.0",
  "channel": "dev",
  "force_update": false,
  "update_full": false,
  "download_url": null,
  "release_notes_url": null,
  "requires_migration": false,
  "min_supported_version": "0.1.0"
}
```

## Manual Update Check

```bash
truenex-mem update check
```

The command performs a GET request to the manifest URL and compares semantic
versions. It prints JSON. It does not apply updates.

## Future Apply Flow

`truenex-mem update apply` is intentionally not implemented yet. Before adding
it, the project must have:

- release artifacts with SHA-256 hashes;
- migration backup/rollback (implemented locally and covered by tests);
- signed or verified manifest strategy;
- e2e tests for install, update, migration, rollback, and data preservation.

## Local Schema Migrations

Schema migrations are explicit and local:

```bash
truenex-mem migrate status
truenex-mem migrate apply
truenex-mem migrate backup-list
truenex-mem migrate restore <backup_filename>
```

`migrate status` reports the current and latest DB schema versions without
creating a missing database. `migrate apply` initializes or updates the local
schema idempotently. If an existing database is present and migrations are
pending, it first copies the DB into `.truenex-memory/backups/`.

`migrate backup-list` lists available backups (newest first), and
`migrate restore` restores a backup to the active database. Restore creates a
pre-overwrite safety backup, validates the backup path stays inside the
configured backups directory, and verifies the restored database is readable.
Both commands support `--json` output.

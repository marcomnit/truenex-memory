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

Release manifest example:

```json
{
  "manifest_version": "1",
  "version": "0.4.0",
  "channel": "stable",
  "force_update": false,
  "update_full": false,
  "download_url": "https://pypi.org/project/truenex-memory/0.4.0/",
  "release_notes_url": "https://github.com/marcomnit/truenex-memory/releases/tag/v0.4.0",
  "requires_migration": true,
  "min_supported_version": "0.3.0"
}
```

## Manual Update Check

```bash
truenex-mem update check
```

The command performs a GET request to the manifest URL and compares semantic
versions. It prints JSON and does not modify the installation.

## Apply An Update

The supported updater upgrades the PyPI package through the detected installer:

```bash
truenex-mem update self
```

It uses `pipx upgrade` for pipx installations and `pip install --upgrade` for
pip installations. Database migrations remain explicit so a backup is created
before changing an existing database:

```bash
truenex-mem migrate status
truenex-mem migrate apply
```

## Publishing

Pushing a tag matching `v*` runs the release workflow. The tag must match the
version in `pyproject.toml`. The workflow:

1. runs unit and end-to-end tests;
2. builds and validates wheel and source distributions;
3. uploads immutable workflow artifacts;
4. creates the GitHub Release;
5. publishes to PyPI through Trusted Publishing.

The PyPI project must authorize the GitHub environment named `pypi` before the
first automated publication. No PyPI token is stored in the repository.

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

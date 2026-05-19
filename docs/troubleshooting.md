# Troubleshooting

## Installation

### `truenex-mem` not found after install

Make sure the Python scripts directory is on your `PATH`. With `pipx` this is handled automatically.

```bash
# Verify the CLI is installed
python -m truenex_memory.cli.main --help
```

### Windows PowerShell execution policy

If venv activation fails:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## Indexing

### `index` includes unwanted files (node_modules, .git, etc.)

Truenex Memory automatically excludes common directories and files:

- `node_modules`, `.git`, `__pycache__`, `.venv`, `build/`, `dist/`
- Lockfiles: `package-lock.json`, `yarn.lock`, `poetry.lock`, `uv.lock`, `Cargo.lock`, etc.

You can add extra exclusions via CLI:

```bash
truenex-mem index . --exclude secret.txt --exclude temp_dir
```

Or place a `.gitignore` in your project root; Truenex Memory respects it during indexing.

## Qdrant

### Qdrant connection timeouts

If Qdrant is slow or unreachable, increase the timeout:

```bash
# Windows
set TRUENEX_MEMORY_QDRANT_TIMEOUT=10

# Linux / macOS
export TRUENEX_MEMORY_QDRANT_TIMEOUT=10
```

If Qdrant is unavailable, Truenex Memory automatically falls back to SQLite vectors.

## Testing

### pytest temp directory permission errors on Windows

If you see `PermissionError` on `pytest-of-marco` or similar:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Temp\pytest-of-*"
```

Or run pytest with a custom base temp directory:

```bash
python -m pytest --basetemp=D:\tmp\pytest
```

## General

### Reset local state

To start fresh in a project:

```bash
rm -rf .truenex-memory
```

Then re-run `truenex-mem init`.

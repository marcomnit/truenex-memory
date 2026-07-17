"""CLI commands for git sync bridge (truenex-mem git ...).

Implements the ``truenex-mem git`` subcommand group:
    init, push, pull, status, remote (add, remove, list, show)

All commands require a **Pro** license.
"""

from __future__ import annotations

import gzip
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer

from truenex_memory.cli.protection import check_license
from truenex_memory.core.config import resolve_project_config
from truenex_memory.git_bridge import GitBridgeError
from truenex_memory.store.repository import MemoryRepository

git_app = typer.Typer(help="Git-based memory synchronization (Pro).")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sync_dir(project_root: Path) -> Path:
    """Return the git sync directory under the project data dir."""
    cfg = resolve_project_config(project_root)
    return cfg.data_dir / "sync"


def _db_path(project_root: Path) -> Path:
    """Return the SQLite database path."""
    return resolve_project_config(project_root).db_path


def _memory_json_path(project_root: Path) -> Path:
    """Return the JSON export path inside the sync directory."""
    return _sync_dir(project_root) / "memory.json"


def _memory_gz_path(project_root: Path) -> Path:
    """Return the gzipped JSON export path inside the sync directory."""
    return _sync_dir(project_root) / "memory.json.gz"


def _manifest_path(project_root: Path) -> Path:
    """Return the manifest path inside the sync directory."""
    return _sync_dir(project_root) / "manifest.json"


def _run_git(
    cwd: Path,
    *args: str,
    check: bool = True,
    capture: bool = True,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a git sub-process in *cwd* and return the result."""
    cmd = ["git", *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _repo_exists(project_root: Path) -> bool:
    """Return ``True`` if a git repository already exists in the sync dir."""
    git_dir = _sync_dir(project_root) / ".git"
    return git_dir.is_dir()


def _ensure_repo(project_root: Path) -> None:
    """Raise ``typer.Exit`` if the git repo has not been initialised."""
    if not _repo_exists(project_root):
        typer.echo(
            "Git sync repository not found. Run 'truenex-mem git init' first.",
            err=True,
        )
        raise typer.Exit(code=1)


def _json_out(data: dict) -> None:
    """Print *data* as pretty-printed JSON."""
    typer.echo(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# git init
# ---------------------------------------------------------------------------


@git_app.command(name="init")
def git_init(
    remote: Annotated[
        Optional[str],
        typer.Option(
            "--remote",
            help="Name of the git remote to configure (default: 'origin').",
            show_default=True,
        ),
    ] = "origin",
    url: Annotated[
        Optional[str],
        typer.Option(
            "--url",
            help="URL of the remote repository to add after init.",
        ),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Output structured JSON instead of human text."),
    ] = False,
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            help="Project root directory.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            readable=True,
            resolve_path=True,
        ),
    ] = Path("."),
) -> None:
    """Initialize the Git sync repository for this project."""
    check_license("pro", json_out=json_out)
    sync = _sync_dir(project_root)
    result: dict = {
        "command": "init",
        "project_root": str(project_root),
        "sync_dir": str(sync),
    }

    if _repo_exists(project_root):
        result["status"] = "skipped"
        result["warning"] = "Repository already exists."
        result["suggestion"] = (
            "Use 'truenex-mem git status' to inspect the current state."
        )
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"⚠️  Git repo already exists at {sync}", err=True)
            typer.echo(
                "   Use 'truenex-mem git status' to inspect the current state.",
                err=True,
            )
        raise typer.Exit(code=0)

    try:
        sync.mkdir(parents=True, exist_ok=True)
        _run_git(sync, "init")

        # Create a minimal .gitignore to protect the DB and the uncompressed JSON
        gitignore = sync / ".gitignore"
        gitignore.write_text("*.db\n*.db-journal\nmemory.json\n", encoding="utf-8")
        _run_git(sync, "add", ".gitignore")
        _run_git(sync, "commit", "-m", "chore: init sync repo")

        if url:
            _run_git(sync, "remote", "add", remote, url)
            result["remote_added"] = {"name": remote, "url": url}

        result["status"] = "ok"
        result["message"] = f"Git repository initialised at {sync}"

        if json_out:
            _json_out(result)
        else:
            typer.echo(f"✅  Git repository initialised at {sync}")
            if url:
                typer.echo(f"   Remote '{remote}' → {url}")

    except subprocess.CalledProcessError as exc:
        result["status"] = "error"
        result["stderr"] = exc.stderr
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  Git init failed: {exc.stderr}", err=True)
        raise typer.Exit(code=1)
    except GitBridgeError as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  {exc}", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# git push
# ---------------------------------------------------------------------------


@git_app.command(name="push")
def git_push(
    remote: Annotated[
        str,
        typer.Option(
            "--remote",
            help="Remote name to push to.",
            show_default=True,
        ),
    ] = "origin",
    branch: Annotated[
        Optional[str],
        typer.Option(
            "--branch",
            help="Branch name to push (default: current branch).",
        ),
    ] = None,
    message: Annotated[
        Optional[str],
        typer.Option(
            "--message",
            "-m",
            help="Custom commit message. Default: 'sync: {timestamp} from {hostname}'.",
        ),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Output structured JSON instead of human text."),
    ] = False,
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            help="Project root directory.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            readable=True,
            resolve_path=True,
        ),
    ] = Path("."),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be done without executing."),
    ] = False,
) -> None:
    """Export the current memory and push to the remote."""
    check_license("pro", json_out=json_out)
    _ensure_repo(project_root)

    sync = _sync_dir(project_root)
    if branch is None:
        branch_proc = _run_git(sync, "branch", "--show-current", check=False)
        branch = branch_proc.stdout.strip() or "main"
    db_path = _db_path(project_root)
    mem_file = _memory_json_path(project_root)
    result: dict = {
        "command": "push",
        "project_root": str(project_root),
        "remote": remote,
        "branch": branch,
        "dry_run": dry_run,
    }

    # Build default commit message if not provided
    if message is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        hostname = platform.node() or "unknown"
        message = f"sync: {ts} from {hostname}"
    result["commit_message"] = message

    if dry_run:
        result["status"] = "dry_run"
        result["steps"] = [
            f"Export DB {db_path} → {mem_file}",
            "git add memory.json manifest.json",
            f"git commit -m '{message}'",
            f"git push {remote} {branch}",
        ]
        if json_out:
            _json_out(result)
        else:
            typer.echo("🔍  Dry-run — no changes made.")
            for step in result["steps"]:
                typer.echo(f"   → {step}")
        raise typer.Exit(code=0)

    try:
        # 1. Export DB → JSON
        if db_path.exists():
            repo = MemoryRepository(db_path)
            payload = repo.export_data()
            mem_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            result["exported_nodes"] = len(payload.get("memory_nodes", []))
        else:
            result["warning"] = f"No database found at {db_path}; creating empty export."
            mem_file.write_text("{}", encoding="utf-8")
            result["exported_nodes"] = 0

        # 2. Compress JSON → gzip
        gz_file = _memory_gz_path(project_root)
        with open(mem_file, "rb") as f_in:
            with gzip.open(gz_file, "wb", compresslevel=9) as f_out:
                f_out.write(f_in.read())

        # 3. Write manifest
        manifest = {
            "version": "0.2.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "node_count": result.get("exported_nodes", 0),
            "hostname": platform.node() or "unknown",
        }
        _manifest_path(project_root).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        # 4. Ensure memory.json is gitignored and remove from tracking if present
        gitignore = sync / ".gitignore"
        ignore_content = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if "memory.json" not in ignore_content.splitlines():
            with open(gitignore, "a", encoding="utf-8") as f:
                f.write("memory.json\n")
        _run_git(sync, "rm", "--cached", "memory.json", check=False)

        # 5. Git add / commit / push
        _run_git(sync, "add", str(gz_file.name))
        _run_git(sync, "add", ".gitignore")
        _run_git(sync, "add", "manifest.json")

        # Check if there is anything to commit
        status_proc = _run_git(sync, "status", "--porcelain")
        if status_proc.stdout.strip():
            _run_git(sync, "commit", "-m", message)
            result["committed"] = True
        else:
            result["committed"] = False

        push_proc = _run_git(sync, "push", remote, branch, check=False, capture=False)
        result["pushed"] = push_proc.returncode == 0
        if push_proc.returncode != 0:
            result["push_stderr"] = push_proc.stderr or "see terminal output"

        result["status"] = "ok"
        if json_out:
            _json_out(result)
        else:
            typer.echo("✅  Push completed.")
            typer.echo(f"   Nodes exported: {result['exported_nodes']}")
            if result["committed"]:
                typer.echo(f"   Committed: {message}")
            else:
                typer.echo("   Nothing new to commit.")
            if result["pushed"]:
                typer.echo(f"   Pushed to {remote}/{branch}.")
            else:
                typer.echo(
                    f"⚠️   Push may have failed: {push_proc.stderr or 'see terminal output'}", err=True
                )

    except subprocess.CalledProcessError as exc:
        result["status"] = "error"
        result["stderr"] = exc.stderr
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  Push failed: {exc.stderr}", err=True)
        raise typer.Exit(code=1)
    except GitBridgeError as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  {exc}", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# git pull
# ---------------------------------------------------------------------------


@git_app.command(name="pull")
def git_pull(
    remote: Annotated[
        str,
        typer.Option(
            "--remote",
            help="Remote name to pull from.",
            show_default=True,
        ),
    ] = "origin",
    branch: Annotated[
        Optional[str],
        typer.Option(
            "--branch",
            help="Branch name to pull (default: current branch).",
        ),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Output structured JSON instead of human text."),
    ] = False,
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            help="Project root directory.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            readable=True,
            resolve_path=True,
        ),
    ] = Path("."),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be done without executing."),
    ] = False,
) -> None:
    """Pull from the remote and import into the local memory."""
    check_license("pro", json_out=json_out)
    _ensure_repo(project_root)

    sync = _sync_dir(project_root)
    if branch is None:
        branch_proc = _run_git(sync, "branch", "--show-current", check=False)
        branch = branch_proc.stdout.strip() or "main"
    db_path = _db_path(project_root)
    mem_file = _memory_json_path(project_root)
    result: dict = {
        "command": "pull",
        "project_root": str(project_root),
        "remote": remote,
        "branch": branch,
        "dry_run": dry_run,
    }

    if dry_run:
        result["status"] = "dry_run"
        result["steps"] = [
            f"git pull {remote} {branch}",
            f"Read {mem_file}",
            f"Import JSON into {db_path}",
        ]
        if json_out:
            _json_out(result)
        else:
            typer.echo("🔍  Dry-run — no changes made.")
            for step in result["steps"]:
                typer.echo(f"   → {step}")
        raise typer.Exit(code=0)

    try:
        pull_proc = _run_git(sync, "pull", "--allow-unrelated-histories", remote, branch, check=False, capture=False)
        result["pull_stdout"] = pull_proc.stdout
        if pull_proc.returncode != 0:
            result["status"] = "error"
            result["pull_stderr"] = pull_proc.stderr or "see terminal output"
            if json_out:
                _json_out(result)
            else:
                typer.echo(f"❌  Pull failed: {pull_proc.stderr or 'see terminal output'}", err=True)
            raise typer.Exit(code=1)

        # Decompress if a gzipped export is present
        gz_file = _memory_gz_path(project_root)
        if gz_file.exists():
            with gzip.open(gz_file, "rb") as f_in:
                with open(mem_file, "wb") as f_out:
                    f_out.write(f_in.read())

        if not mem_file.exists():
            result["status"] = "ok"
            result["warning"] = (
                f"No memory file found at {mem_file}; nothing to import."
            )
            result["imported_nodes"] = 0
            if json_out:
                _json_out(result)
            else:
                typer.echo("✅  Pull completed, but no memory.json to import.")
            raise typer.Exit(code=0)

        # Import JSON → DB
        payload = json.loads(mem_file.read_text(encoding="utf-8"))
        repo = MemoryRepository(db_path)
        repo.import_data(payload)
        imported = len(payload.get("memory_nodes", []))
        result["imported_nodes"] = imported
        result["status"] = "ok"

        if json_out:
            _json_out(result)
        else:
            typer.echo("✅  Pull & import completed.")
            typer.echo(f"   Imported nodes: {imported}")

    except subprocess.CalledProcessError as exc:
        result["status"] = "error"
        result["stderr"] = exc.stderr
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  Pull failed: {exc.stderr}", err=True)
        raise typer.Exit(code=1)
    except GitBridgeError as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  {exc}", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# git status
# ---------------------------------------------------------------------------


@git_app.command(name="status")
def git_status(
    short: Annotated[
        bool,
        typer.Option("--short", help="Show compact one-line status."),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Output structured JSON instead of human text."),
    ] = False,
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            help="Project root directory.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            readable=True,
            resolve_path=True,
        ),
    ] = Path("."),
) -> None:
    """Show sync status between the local DB and the Git repository."""
    check_license("pro", json_out=json_out)
    sync = _sync_dir(project_root)
    db_path = _db_path(project_root)
    mem_file = _memory_json_path(project_root)
    manifest_file = _manifest_path(project_root)

    result: dict = {"command": "status", "project_root": str(project_root)}

    # Check if repo exists
    if not _repo_exists(project_root):
        result["status"] = "not_initialised"
        result["message"] = "Git sync repository not found."
        if json_out:
            _json_out(result)
        else:
            typer.echo(
                "⚠️  Git sync repository not found. Run 'truenex-mem git init' first.",
                err=True,
            )
        raise typer.Exit(code=1)

    try:
        # DB node count
        db_nodes = 0
        if db_path.exists():
            repo = MemoryRepository(db_path)
            db_nodes = len(repo.list_memory_nodes())
        result["db_nodes"] = db_nodes

        # JSON node count (prefer gzipped export if present)
        mem_nodes = 0
        gz_file = _memory_gz_path(project_root)
        if gz_file.exists():
            with gzip.open(gz_file, "rb") as f_in:
                payload = json.loads(f_in.read().decode("utf-8"))
            mem_nodes = len(payload.get("memory_nodes", []))
        elif mem_file.exists():
            payload = json.loads(mem_file.read_text(encoding="utf-8"))
            mem_nodes = len(payload.get("memory_nodes", []))
        result["memory_json_nodes"] = mem_nodes

        # Manifest hash
        manifest_hash: Optional[str] = None
        if manifest_file.exists():
            import hashlib

            manifest_hash = hashlib.sha256(manifest_file.read_bytes()).hexdigest()[:12]
        result["manifest_hash"] = manifest_hash

        # Git branch
        branch_proc = _run_git(sync, "branch", "--show-current", check=False)
        current_branch = (
            branch_proc.stdout.strip() if branch_proc.returncode == 0 else "unknown"
        )
        result["branch"] = current_branch

        # Git status --porcelain for untracked/modified
        status_proc = _run_git(sync, "status", "--porcelain", check=False)
        dirty_lines = [line for line in status_proc.stdout.splitlines() if line.strip()]
        result["dirty_files"] = dirty_lines
        result["dirty"] = bool(dirty_lines)

        result["status"] = "ok"

        if json_out:
            _json_out(result)
            return

        if short:
            dirty_marker = "*" if result["dirty"] else ""
            typer.echo(
                f"{current_branch}{dirty_marker}  DB:{db_nodes}  MEM:{mem_nodes}  "
                f"manifest:{manifest_hash or 'none'}"
            )
            return

        typer.echo("─── Git Sync Status ───")
        typer.echo(f"Branch:       {current_branch}")
        typer.echo(f"DB nodes:     {db_nodes}")
        typer.echo(f"MEM nodes:    {mem_nodes}")
        typer.echo(f"Manifest:     {manifest_hash or 'none'}")
        if dirty_lines:
            typer.echo(f"Dirty files:  {len(dirty_lines)}")
            for line in dirty_lines:
                typer.echo(f"   {line}")
        else:
            typer.echo("Working tree: clean")

    except subprocess.CalledProcessError as exc:
        result["status"] = "error"
        result["stderr"] = exc.stderr
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  Status check failed: {exc.stderr}", err=True)
        raise typer.Exit(code=1)
    except GitBridgeError as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  {exc}", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# git remote  (sub-typer)
# ---------------------------------------------------------------------------

remote_app = typer.Typer(help="Manage Git remotes.")
git_app.add_typer(remote_app, name="remote")


@remote_app.command(name="add")
def remote_add(
    name: Annotated[
        str,
        typer.Argument(help="Name of the remote."),
    ],
    url: Annotated[
        str,
        typer.Argument(help="URL of the remote repository."),
    ],
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Output structured JSON instead of human text."),
    ] = False,
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            help="Project root directory.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            readable=True,
            resolve_path=True,
        ),
    ] = Path("."),
) -> None:
    """Add a new git remote."""
    check_license("pro", json_out=json_out)
    _ensure_repo(project_root)
    sync = _sync_dir(project_root)
    result: dict = {"command": "remote add", "name": name, "url": url}

    try:
        _run_git(sync, "remote", "add", name, url)
        result["status"] = "ok"
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"✅  Remote '{name}' added → {url}")
    except subprocess.CalledProcessError as exc:
        result["status"] = "error"
        result["stderr"] = exc.stderr
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  Failed to add remote: {exc.stderr}", err=True)
        raise typer.Exit(code=1)


@remote_app.command(name="remove")
def remote_remove(
    name: Annotated[
        str,
        typer.Argument(help="Name of the remote to remove."),
    ],
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Output structured JSON instead of human text."),
    ] = False,
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            help="Project root directory.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            readable=True,
            resolve_path=True,
        ),
    ] = Path("."),
) -> None:
    """Remove a git remote."""
    check_license("pro", json_out=json_out)
    _ensure_repo(project_root)
    sync = _sync_dir(project_root)
    result: dict = {"command": "remote remove", "name": name}

    try:
        _run_git(sync, "remote", "remove", name)
        result["status"] = "ok"
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"✅  Remote '{name}' removed.")
    except subprocess.CalledProcessError as exc:
        result["status"] = "error"
        result["stderr"] = exc.stderr
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  Failed to remove remote: {exc.stderr}", err=True)
        raise typer.Exit(code=1)


@remote_app.command(name="list")
def remote_list(
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Output structured JSON instead of human text."),
    ] = False,
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            help="Project root directory.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            readable=True,
            resolve_path=True,
        ),
    ] = Path("."),
) -> None:
    """List all configured git remotes."""
    check_license("pro", json_out=json_out)
    _ensure_repo(project_root)
    sync = _sync_dir(project_root)
    result: dict = {"command": "remote list"}

    try:
        proc = _run_git(sync, "remote", "-v", check=False)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]

        # Parse remotes
        remotes: dict[str, list[dict[str, str]]] = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                r_name, r_url, r_type = parts[0], parts[1], parts[2].strip("()")
                remotes.setdefault(r_name, []).append(
                    {"url": r_url, "type": r_type}
                )

        result["remotes"] = remotes
        result["status"] = "ok"

        if json_out:
            _json_out(result)
        else:
            if not remotes:
                typer.echo("(no remotes configured)")
            else:
                for r_name, entries in remotes.items():
                    for entry in entries:
                        typer.echo(f"{r_name}\t{entry['url']} ({entry['type']})")

    except subprocess.CalledProcessError as exc:
        result["status"] = "error"
        result["stderr"] = exc.stderr
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  Failed to list remotes: {exc.stderr}", err=True)
        raise typer.Exit(code=1)


@remote_app.command(name="show")
def remote_show(
    name: Annotated[
        str,
        typer.Argument(help="Name of the remote to show."),
    ],
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Output structured JSON instead of human text."),
    ] = False,
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            help="Project root directory.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            readable=True,
            resolve_path=True,
        ),
    ] = Path("."),
) -> None:
    """Show detailed information about a git remote."""
    check_license("pro", json_out=json_out)
    _ensure_repo(project_root)
    sync = _sync_dir(project_root)
    result: dict = {"command": "remote show", "name": name}

    try:
        proc = _run_git(sync, "remote", "show", name, check=False)
        if proc.returncode != 0:
            result["status"] = "error"
            result["stderr"] = proc.stderr
            if json_out:
                _json_out(result)
            else:
                typer.echo(f"❌  Remote '{name}' not found.", err=True)
            raise typer.Exit(code=1)

        result["output"] = proc.stdout
        result["status"] = "ok"

        if json_out:
            _json_out(result)
        else:
            typer.echo(proc.stdout)

    except subprocess.CalledProcessError as exc:
        result["status"] = "error"
        result["stderr"] = exc.stderr
        if json_out:
            _json_out(result)
        else:
            typer.echo(f"❌  Failed to show remote: {exc.stderr}", err=True)
        raise typer.Exit(code=1)

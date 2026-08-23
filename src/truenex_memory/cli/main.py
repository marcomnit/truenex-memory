"""CLI entry point for Truenex Memory."""

from dataclasses import asdict
from pathlib import Path
import json
import os
import sys

import typer

from truenex_memory import __version__
from truenex_memory.adapters.agents_md import generate_agents_md
from truenex_memory.adapters.claude_md import generate_claude_md
from truenex_memory.core.memory_service import MemoryService
from truenex_memory.diagnostics.doctor import run_doctor
from truenex_memory.export.exporter import export_memory
from truenex_memory.export.importer import import_memory
from truenex_memory.mcp.server import run_stdio_server
from truenex_memory.release.auto_update_check import check_and_notify
from truenex_memory.release.manifest import DEFAULT_MANIFEST_URL
from truenex_memory.release.self_update import run_self_update
from truenex_memory.release.update_check import check_for_updates
from truenex_memory.release.version import get_version_info
from truenex_memory.core.config import resolve_project_config
from truenex_memory.core.migration import list_backups
from truenex_memory.core.migration import migrate_apply as apply_migrations
from truenex_memory.core.migration import migration_status
from truenex_memory.core.migration import restore_backup
from truenex_memory.discovery.agent_discovery import (
    DEFAULT_DISPLAY_LIMIT,
    add_agent_to_manifest,
    discover_from_agents,
    format_report,
    get_effective_agent_roots,
    heuristic_discovery,
    load_agent_manifest,
    remove_agent_from_manifest,
)
from truenex_memory.discovery.source_catalog import (
    CatalogEntry,
    SourceCatalog,
    default_catalog_path,
    entries_to_dict,
    format_entries,
    report_to_entries,
    source_id,
)
from truenex_memory.ingestion.engine import ingest_manifest
from truenex_memory.ingestion.global_refresh import (
    RefreshReport,
    format_refresh_report,
    refresh as run_global_refresh,
)
from truenex_memory.ingestion.global_context import (
    build_project_context,
    format_context_report,
)
from truenex_memory.ingestion.global_search import (
    DEFAULT_GLOBAL_SEARCH_LIMIT,
    GLOBAL_SEARCH_KINDS,
    build_global_search,
    format_global_search_report,
)
from truenex_memory.ingestion.reindex_embeddings import (
    DEFAULT_REINDEX_BATCH_SIZE,
    reindex_embeddings,
)
from truenex_memory.ingestion.global_status import (
    build_global_status,
    format_status_report,
)
from truenex_memory.ingestion.global_source_health import (
    build_source_health,
    format_source_health_report,
)
from truenex_memory.ingestion.ledger_purge import (
    format_ledger_purge_report,
    purge_missing_ledger_entries,
)
from truenex_memory.ingestion.global_auto_status import (
    build_auto_status,
    format_auto_status_report,
)
from truenex_memory.ingestion.global_auto_review import (
    DEFAULT_CONTENT_CHARS,
    DEFAULT_REVIEW_LIMIT,
    build_auto_memory_review,
    format_auto_memory_review,
)
from truenex_memory.ingestion.global_auto_lifecycle import (
    CURATED_AUTO_MEMORY_TYPES,
    DEFAULT_PRUNE_LIMIT,
    approve_auto_memory,
    format_auto_memory_lifecycle_report,
    promote_auto_memory,
    prune_auto_memories,
    reject_auto_memory,
)
from truenex_memory.ingestion.global_auto_memory import (
    DEFAULT_AUTO_MEMORY_LIMIT,
    DEFAULT_AUTO_MEMORY_PER_SOURCE_LIMIT,
    DEFAULT_CONFIDENCE,
    generate_unverified_auto_memories,
)
from truenex_memory.retrieval.result import search_payload
from truenex_memory.store.models import VALID_STATUSES
from truenex_memory.cli.task_commands import task_app
from truenex_memory.cli.orchestrate_commands import orchestrate_app
from truenex_memory.cli.license_commands import license_app
from truenex_memory.cli.git_commands import git_app
from truenex_memory.cli.protection import check_license

def resolve_project_root() -> str:
    """Restituisce il project root da usare: env > locale."""
    env = os.environ.get("TRUENEX_PROJECT_ROOT", "").strip()
    if env:
        return env
    return "."


app = typer.Typer(
    name="truenex-mem",
    help="Local-first memory layer for coding agents.",
)
adapter_app = typer.Typer(help="Generate local agent adapter files.")
update_app = typer.Typer(help="Manual update checks.")
migrate_app = typer.Typer(help="Schema migration management.")
status_app = typer.Typer(help="Manage memory node lifecycle status.")
ingest_app = typer.Typer(help="Ingest external sources from a manifest.")
trace_app = typer.Typer(help="Inspect retrieval trace logs.")
global_app = typer.Typer(help="Global store operations (discovery, refresh, status).")
sources_app = typer.Typer(help="Review, confirm, and add source catalog entries.")
auto_app = typer.Typer(help="Automatic memory maintenance (Phase 3).")
agent_app = typer.Typer(help="Manage agent discovery manifest.")
graph_app = typer.Typer(help="Code-structure graph (relations between files).")
profile_app = typer.Typer(help="Il profilo di comportamento consegnato ai client agentici.")
app.add_typer(adapter_app, name="adapter")
app.add_typer(update_app, name="update")
app.add_typer(migrate_app, name="migrate")
app.add_typer(status_app, name="status")
app.add_typer(ingest_app, name="ingest")
app.add_typer(trace_app, name="trace")
global_app.add_typer(sources_app, name="sources")
global_app.add_typer(auto_app, name="auto")
app.add_typer(global_app, name="global")
app.add_typer(profile_app, name="profile")
app.add_typer(graph_app, name="graph")
app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")
app.add_typer(orchestrate_app, name="orchestrate")
app.add_typer(license_app, name="license")
app.add_typer(git_app, name="git")


@app.callback()
def callback(ctx: typer.Context) -> None:
    """Truenex Memory - local-first memory for coding agents."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    # Skip auto-update check for MCP/serve to avoid stderr interference
    if ctx.invoked_subcommand not in ("mcp", "serve"):
        check_and_notify(__version__)


@app.command()
def version() -> None:
    """Print the Truenex Memory version."""
    print(f"truenex-mem {__version__}")


@app.command("version-info")
def version_info() -> None:
    """Print all Truenex Memory component versions as JSON."""

    typer.echo(json.dumps(get_version_info(), indent=2, sort_keys=True))


@app.command()
def init() -> None:
    """Initialize local project memory storage."""

    service = MemoryService(resolve_project_root())
    service.init_project()
    typer.echo(f"Initialized {service.config.data_dir}")


@app.command()
def add(
    content: str = typer.Argument(..., help="Memory content to store."),
    memory_type: str = typer.Option(
        "note",
        "--type",
        help="Memory type: note, decision, issue, or pattern.",
    ),
    supersedes: str | None = typer.Option(
        None,
        "--supersedes",
        help=(
            "Id of a memory this one replaces. That memory becomes "
            "'superseded' and drops out of search, while still pointing at "
            "this one. Use it whenever a note corrects or updates an "
            "earlier one, so a stale claim stops being retrieved as fact."
        ),
    ),
) -> None:
    """Add a manual memory node."""

    memory_id = MemoryService(resolve_project_root()).add(
        content, memory_type=memory_type, supersedes=supersedes
    )
    typer.echo(memory_id)
    if supersedes:
        typer.echo(f"superseded {supersedes}", err=True)


@app.command("list")
def list_command(
    status: str | None = typer.Option(None, "--status", help="Filter by lifecycle status."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """List manual memory nodes."""

    if status is not None:
        _validate_status(status)
    memories = MemoryService(resolve_project_root()).list_memory_nodes(status=status)
    if json_output:
        typer.echo(json.dumps([asdict(memory) for memory in memories], indent=2, sort_keys=True))
        return
    for memory in memories:
        typer.echo(f"{memory.id} {memory.status} {memory.type} {memory.title}")


@app.command()
def index(
    path: Path = typer.Argument(Path("."), help="File or directory to index."),
    chunk_size: int = typer.Option(0, "--chunk-size", help="Max chars per chunk (0 = use config default)."),
    chunk_overlap: int = typer.Option(0, "--chunk-overlap", help="Overlap chars between chunks (0 = none)."),
    exclude: list[str] = typer.Option([], "--exclude", help="Additional directory or filename patterns to exclude."),
) -> None:
    """Index local files into the project memory store."""

    if not path.exists():
        raise typer.BadParameter(f"path does not exist: {path}")
    service = MemoryService(resolve_project_root())
    extra_dirs = set()
    extra_filenames = set()
    for pat in exclude:
        if "/" in pat or "\\" in pat:
            # Treat as directory path component if it looks like one
            extra_dirs.add(pat.strip("/\\"))
        else:
            extra_filenames.add(pat)
    count = service.index(
        path,
        chunk_size=chunk_size if chunk_size > 0 else None,
        chunk_overlap=chunk_overlap if chunk_overlap > 0 else None,
        extra_dirs=extra_dirs or None,
        extra_filenames=extra_filenames or None,
    )
    typer.echo(f"Indexed {count} file(s)")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    top_k: int = typer.Option(5, "--top-k", min=1, max=50, help="Maximum results."),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON payload."),
    include_inactive: bool = typer.Option(
        False, "--include-inactive", help="Include inactive (e.g. obsolete) memories in results."
    ),
    scope: str | None = typer.Option(
        None,
        "--scope",
        help=(
            "Restrict document results to paths containing this substring, "
            "normally the project you are asking about. The store holds every "
            "project at once, so an unscoped search competes against roughly "
            "eighty times more candidates; scoping tripled the answers found "
            "on questions phrased without the document's own words. Omit it "
            "for cross-project questions."
        ),
    ),
) -> None:
    """Search local memory."""

    service = MemoryService(resolve_project_root())
    results = service.search(
        query, top_k=top_k, include_inactive=include_inactive, scope=scope
    )
    payload = search_payload(query, results, trace_id=service.last_trace_id)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    for item in payload["results"]:
        typer.echo(f"{item['score']:.4f} {item['title']} [{item['memory_type']}/{item['status']}]")
        if item["source_path"]:
            typer.echo(f"  source: {item['source_path']}")
        typer.echo(f"  {item['content']}")


@app.command("logs")
def logs_command(
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=100, help="Number of recent logs."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """List recent retrieval trace logs."""

    service = MemoryService(resolve_project_root())
    logs = service.list_retrieval_logs(limit=limit)
    if json_output:
        items = [
            {
                "id": log.id,
                "trace_id": log.id,
                "query": log.query,
                "result_count": log.result_count,
                "top_k": log.top_k,
                "created_at": log.created_at,
            }
            for log in logs
        ]
        typer.echo(json.dumps(items, indent=2, sort_keys=True))
        return
    if not logs:
        typer.echo("No retrieval logs found.")
        return
    for log in logs:
        typer.echo(f"{log.id} | {log.result_count}/{log.top_k} | {log.query}")


@trace_app.command("show")
def trace_show(
    trace_id: str = typer.Argument(..., help="Trace ID from a search or logs command."),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON payload."),
) -> None:
    """Show a retrieval trace by ID with full result details."""

    service = MemoryService(resolve_project_root())
    log = service.get_retrieval_log(trace_id)
    if log is None:
        raise typer.BadParameter(f"trace not found: {trace_id!r}")
    payload = {
        "trace_id": log.id,
        "query": log.query,
        "top_k": log.top_k,
        "result_count": log.result_count,
        "created_at": log.created_at,
        "results": log.parsed_results(),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"Trace: {log.id}")
    typer.echo(f"Query: {log.query}")
    typer.echo(f"Results: {log.result_count}/{log.top_k}  |  {log.created_at}")
    typer.echo("")
    results = log.parsed_results()
    for item in results:
        typer.echo(
            f"{item.get('score', 0):.4f} {item.get('title', '?')} "
            f"[{item.get('memory_type', '?')}/{item.get('status', '?')}]"
        )
        if item.get("source_path"):
            typer.echo(f"  source: {item['source_path']}")
        if item.get("heading_path"):
            typer.echo(f"  heading: {item['heading_path']}")
        typer.echo(f"  {item.get('content', '')}")


@migrate_app.command("status")
def migrate_status(
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Show current and latest schema versions."""
    config = resolve_project_config(".")
    status = migration_status(config.db_path)

    if json_output:
        typer.echo(json.dumps(status, indent=2, sort_keys=True))
        return

    typer.echo(f"Current schema version: {status['current_version']}")
    typer.echo(f"Latest schema version:  {status['latest_version']}")
    typer.echo("Status: migrations pending" if status["pending"] else "Status: up to date")


@migrate_app.command("apply")
def migrate_apply(
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Apply pending schema migrations (with automatic pre-migration backup)."""
    config = resolve_project_config(".")
    result = apply_migrations(config.db_path, config.backups_dir)

    if json_output:
        payload = {k: v for k, v in result.items()}
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if not result["applied"]:
        typer.echo("Already up to date, no migrations applied.")
        return

    typer.echo(f"Applied migrations: {result['previous_version']} -> {result['current_version']}")
    if result["backup_path"]:
        typer.echo(f"Backup created at: {result['backup_path']}")


@migrate_app.command("backup-list")
def migrate_backup_list(
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """List available migration backups (newest first)."""
    config = resolve_project_config(".")
    backups = list_backups(config.backups_dir)

    if json_output:
        typer.echo(json.dumps(backups, indent=2, sort_keys=True))
        return

    if not backups:
        typer.echo("No migration backups found.")
        return

    for entry in backups:
        size_kb = int(entry["size_bytes"]) / 1024  # type: ignore[arg-type]
        typer.echo(
            f"{entry['filename']}  {size_kb:.1f} KiB  {entry['created']}"
        )


@migrate_app.command("restore")
def migrate_restore(
    backup_filename: str = typer.Argument(..., help="Backup filename to restore."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Restore a migration backup to the active database.

    A safety backup of the current database is created before overwriting.
    """
    config = resolve_project_config(".")
    try:
        result = restore_backup(config.db_path, config.backups_dir, backup_filename)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        if json_output:
            typer.echo(
                json.dumps({"error": str(exc)}, indent=2, sort_keys=True)
            )
            raise typer.Exit(code=1)
        raise typer.BadParameter(str(exc)) from exc

    if json_output:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return

    typer.echo(f"Restored: {result['backup_filename']}")
    typer.echo(f"Current schema version: {result['current_version']}")
    if result["safety_backup_path"]:
        typer.echo(f"Safety backup: {result['safety_backup_path']}")


@app.command()
def doctor(privacy: bool = typer.Option(False, "--privacy", help="Include privacy diagnostics.")) -> None:
    """Run local diagnostics."""

    typer.echo(json.dumps(run_doctor(".", privacy=privacy), indent=2, sort_keys=True))


@app.command("export")
def export_command(output: Path = typer.Option(..., "--output", "-o", help="Output JSON file.")) -> None:
    """Export local memory data."""

    exported = export_memory(output, project_root=".")
    typer.echo(f"Exported {exported}")


@app.command("import")
def import_command(input_path: Path = typer.Argument(..., help="Memory export JSON file.")) -> None:
    """Import local memory data."""

    import_memory(input_path, project_root=".")
    typer.echo(f"Imported {input_path}")


@app.command()
def mcp(
    project_root: Path = typer.Option(
        Path("."),
        "--project-root",
        help="Project root used for local memory storage.",
    ),
) -> None:
    """Run the local stdio memory tool server."""

    run_stdio_server(project_root=project_root)


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind address for the HTTP server.",
    ),
    port: int = typer.Option(
        8000,
        "--port",
        "-p",
        help="Bind port for the HTTP server.",
    ),
    project_root: Path = typer.Option(
        Path("."),
        "--project-root",
        help="Project root used for local memory storage.",
    ),
) -> None:
    """Start the HTTP API server (for the Truenex Memory Desktop GUI)."""

    check_license("pro")
    from truenex_memory.serve import run_serve
    run_serve(host=host, port=port, project_root=str(project_root))


@status_app.command("set")
def status_set(
    memory_id: str = typer.Argument(..., help="Memory node id."),
    status: str = typer.Argument(..., help="New lifecycle status."),
) -> None:
    """Set a memory node lifecycle status."""

    _validate_status(status)
    try:
        MemoryService(resolve_project_root()).set_memory_status(memory_id, status)
    except LookupError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Updated {memory_id} -> {status}")


@adapter_app.command("agents-md")
def adapter_agents_md() -> None:
    """Print AGENTS.md instructions."""

    typer.echo(generate_agents_md())


@adapter_app.command("claude-md")
def adapter_claude_md() -> None:
    """Print CLAUDE.md instructions."""

    typer.echo(generate_claude_md())


@update_app.command(name="self")
def update_self() -> None:
    """Upgrade truenex-memory to the latest version (pipx or pip)."""
    sys.exit(run_self_update())


@update_app.command("check")
def update_check(
    manifest_url: str = typer.Option(
        DEFAULT_MANIFEST_URL,
        "--manifest-url",
        help="Public JSON manifest URL.",
    ),
) -> None:
    """Check for updates without sending project data."""

    result = check_for_updates(manifest_url=manifest_url)
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@ingest_app.command("manifest")
def ingest_manifest_command(
    manifest: Path = typer.Option(
        ...,
        "--manifest",
        "-m",
        help="Path to the source manifest JSON file.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and report without indexing."),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
    project_root: Path = typer.Option(
        Path("."),
        "--project-root",
        help="Project root for memory storage and relative path resolution.",
    ),
) -> None:
    """Ingest sources declared in a manifest file.

    The manifest is a JSON file listing sources with source_type, source_path,
    and optional source_tool / privacy_scope fields.

    Supported source_type values:
      project_docs  - text project files (md, py, toml, etc.)
      agent_session - Codex/Claude-style JSONL session logs

    Future (parse_later):
      agent_memory, operations_note, binary_document

    Dry-run reports which sources would be indexed, deferred, skipped, or in
    error without modifying the database.
    """
    service = MemoryService(project_root)
    if not dry_run:
        service.init_project()

    report = ingest_manifest(
        manifest_path=manifest.resolve(),
        project_root=service.config.project_root,
        repository=service.repository,
        dry_run=dry_run,
    )

    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
        return

    _print_ingest_report(report, dry_run)


@global_app.command("discover")
def global_discover(
    from_agents: bool = typer.Option(
        True, "--from-agents", help="Discover from local agent client directories."
    ),
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory containing .codex / .claude agent roots.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write report to this file (JSON or .md)."
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        "-n",
        min=1,
        max=500,
        help="Max entries per section (default 20 for text, unlimited for JSON).",
    ),
) -> None:
    """Discover projects, docs, and servers from local agent clients.

    Scans configured agent roots (Codex, Claude, Kimi, Cursor, OpenClaw,
    Aider, Antigravity, Gemini, plus any user-discovered or custom roots)
    to find:
    - Candidate project paths
    - Document references
    - SSH/server aliases

    This is discovery only -- it does not modify the memory database.
    """
    if not from_agents:
        typer.echo("Currently only --from-agents discovery is supported.")
        raise typer.Exit(code=2)

    report = discover_from_agents(home)

    if output is not None:
        suffix = output.suffix.lower()
        if suffix == ".json":
            d = report.to_dict()
            if limit is not None:
                d["projects"] = d["projects"][:limit]
                d["documents"] = d["documents"][:limit]
                d["servers"] = d["servers"][:limit]
            output.write_text(
                json.dumps(d, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        else:
            display_limit = limit if limit is not None else DEFAULT_DISPLAY_LIMIT
            output.write_text(format_report(report, limit=display_limit), encoding="utf-8")
        typer.echo(f"Report written to {output}")
        return

    if json_output:
        d = report.to_dict()
        if limit is not None:
            d["projects"] = d["projects"][:limit]
            d["documents"] = d["documents"][:limit]
            d["servers"] = d["servers"][:limit]
        typer.echo(json.dumps(d, indent=2, sort_keys=True))
    else:
        display_limit = limit if limit is not None else DEFAULT_DISPLAY_LIMIT
        typer.echo(format_report(report, limit=display_limit))


@global_app.command("scan-agents")
def global_scan_agents(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory to scan for agent roots.",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Interactively ask which discovered agents to include.",
    ),
    include_all: bool = typer.Option(
        False,
        "--include-all",
        "-a",
        help="Automatically include all discovered agents.",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Reset discovery preferences (remove all heuristic inclusions).",
    ),
) -> None:
    """Scan for new agent directories and optionally add them to discovery."""
    from truenex_memory.discovery.agent_discovery import (
        _load_discovery_prefs,
        _save_discovery_prefs,
    )

    if reset:
        prefs = _load_discovery_prefs()
        prefs["included_heuristic"] = []
        _save_discovery_prefs(prefs)
        typer.echo("✅  Discovery preferences reset.")
        raise typer.Exit(code=0)

    current = get_effective_agent_roots()
    typer.echo("─── Currently configured agents ───")
    for label, rel, sub in current:
        path = home / rel / sub
        status = "✅ found" if path.exists() else "⚠️  not found"
        typer.echo(f"  {label:<30} {status}")

    discovered = heuristic_discovery(home)
    known = {(rel, sub) for _, rel, sub in current}
    new = [d for d in discovered if (d[1], d[2]) not in known]

    if not new:
        typer.echo("\n✅  No new agent directories discovered.")
        raise typer.Exit(code=0)

    typer.echo(f"\n─── Discovered {len(new)} new agent directories ───")
    for label, rel, sub in new:
        typer.echo(f"  {label:<30} (~/{rel}/{sub})")

    if include_all:
        prefs = _load_discovery_prefs()
        for label, rel, sub in new:
            prefs["included_heuristic"].append({
                "label": label,
                "root": rel,
                "subdir": sub,
            })
        _save_discovery_prefs(prefs)
        typer.echo(f"\n✅  Included all {len(new)} discovered agents.")
        raise typer.Exit(code=0)

    if interactive:
        prefs = _load_discovery_prefs()
        included = 0
        for label, rel, sub in new:
            if typer.confirm(f"Include {label} (~/{rel}/{sub})?"):
                prefs["included_heuristic"].append({
                    "label": label,
                    "root": rel,
                    "subdir": sub,
                })
                included += 1
        _save_discovery_prefs(prefs)
        typer.echo(f"\n✅  Included {included} new agents.")
        raise typer.Exit(code=0)

    typer.echo(
        "\nTip: use --interactive (-i) to choose which to include, "
        "or --include-all (-a) to add all."
    )


@agent_app.command("list")
def agent_list() -> None:
    """List all agents in the discovery manifest."""
    manifest = load_agent_manifest()
    agents = manifest.get("agents", [])
    if not agents:
        typer.echo("No agents configured in manifest.")
        raise typer.Exit(code=0)
    typer.echo("─── Agent discovery manifest ───")
    for agent in agents:
        name = agent.get("name", "unknown")
        rel_dir = agent.get("dir", "")
        roots = agent.get("roots", [])
        typer.echo(f"  {name}")
        for root in roots:
            label = root.get("label", "")
            subdir = root.get("subdir", "")
            typer.echo(f"    {label}: ~/{rel_dir}/{subdir}")


@agent_app.command("add")
def agent_add(
    name: str = typer.Argument(..., help="Agent name (e.g. windsurf)."),
    dir: str = typer.Option(..., "--dir", "-d", help="Hidden directory name (e.g. .windsurf)."),
    subdir: str = typer.Option(..., "--subdir", "-s", help="Subdirectory to scan (e.g. sessions)."),
    label: str | None = typer.Option(None, "--label", "-l", help="Root label (defaults to subdir)."),
) -> None:
    """Add a new agent to the discovery manifest."""
    add_agent_to_manifest(name, dir, subdir, label=label)
    typer.echo(f"✅  Added agent '{name}' -> ~/{dir}/{subdir}")


@agent_app.command("remove")
def agent_remove(
    name: str = typer.Argument(..., help="Agent name to remove."),
) -> None:
    """Remove an agent from the discovery manifest."""
    remove_agent_from_manifest(name)
    typer.echo(f"✅  Removed agent '{name}'.")


@sources_app.command("review")
def sources_review(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory containing .codex / .claude agent roots.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print entries as JSON."),
    limit: int | None = typer.Option(
        None,
        "--limit",
        "-n",
        min=1,
        max=500,
        help="Max entries per section (default 20 for text, unlimited for JSON).",
    ),
    include: list[str] | None = typer.Option(
        None,
        "--include",
        "-i",
        help="Keep entries whose id/path/project/source contains any of these texts. Repeatable.",
    ),
    exclude: list[str] | None = typer.Option(
        None,
        "--exclude",
        "-x",
        help="Drop entries whose id/path/project/source contains this text. Repeatable.",
    ),
    source_type: list[str] | None = typer.Option(
        None,
        "--source-type",
        help="Keep only entries of this source type. Repeatable.",
    ),
) -> None:
    """Review discovered source candidates without writing the catalog.

    Runs discovery from agent roots and prints candidate catalog entries.
    No files or databases are mutated.
    """
    report = discover_from_agents(home)
    effective_limit = limit if limit is not None else (None if json_output else DEFAULT_DISPLAY_LIMIT)
    entries = report_to_entries(
        report,
        limit=effective_limit,
        confirmation_status="candidate",
    )
    entries = _filter_catalog_entries(
        entries,
        include=include,
        exclude=exclude,
        source_type=source_type,
    )

    if json_output:
        typer.echo(json.dumps(entries_to_dict(entries), indent=2, sort_keys=True))
    else:
        typer.echo(format_entries(entries))


@sources_app.command("confirm")
def sources_confirm(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory containing .codex / .claude agent roots.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Path to the source catalog JSON file (default: <home>/.truenex-memory/sources.json).",
    ),
    limit: int | None = typer.Option(
        DEFAULT_DISPLAY_LIMIT,
        "--limit",
        "-n",
        min=1,
        max=500,
        help="Max entries per section to confirm.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    json_output: bool = typer.Option(False, "--json", help="Print entries as JSON."),
    include: list[str] | None = typer.Option(
        None,
        "--include",
        "-i",
        help="Keep entries whose id/path/project/source contains any of these texts. Repeatable.",
    ),
    exclude: list[str] | None = typer.Option(
        None,
        "--exclude",
        "-x",
        help="Drop entries whose id/path/project/source contains this text. Repeatable.",
    ),
    source_type: list[str] | None = typer.Option(
        None,
        "--source-type",
        help="Keep only entries of this source type. Repeatable.",
    ),
) -> None:
    """Confirm discovered sources and write the catalog.

    Runs discovery from agent roots, converts candidates to catalog entries,
    and writes confirmed entries to the catalog JSON file.

    By default only the top-ranked subset per section is confirmed.
    Use --limit to adjust the count or pass a large value to confirm more.
    """
    report = discover_from_agents(home)
    entries = report_to_entries(report, limit=limit, confirmation_status="confirmed")
    entries = _filter_catalog_entries(
        entries,
        include=include,
        exclude=exclude,
        source_type=source_type,
    )
    catalog_path = catalog if catalog is not None else default_catalog_path(home)

    if json_output:
        typer.echo(json.dumps(entries_to_dict(entries), indent=2, sort_keys=True))

    if not yes:
        count = len(entries)
        prompt_text = f"Confirm writing {count} entries to {catalog_path}? [y/N] "
        try:
            answer = input(prompt_text).strip().lower()
        except (EOFError, KeyboardInterrupt):
            typer.echo("Aborted.")
            raise typer.Exit(code=1)
        if answer not in ("y", "yes"):
            typer.echo("Aborted.")
            raise typer.Exit(code=1)

    sc = SourceCatalog(entries=entries)
    sc.save(catalog_path)
    typer.echo(f"Catalog written: {len(entries)} entries to {catalog_path}")


_VALID_SOURCE_TYPES = frozenset({"agent_root", "project_root", "document", "server_alias"})


@sources_app.command("add")
def sources_add(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default catalog path.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Path to the source catalog JSON file (default: <home>/.truenex-memory/sources.json).",
    ),
    source_type: str = typer.Option(
        ...,
        "--source-type",
        help="Source type: agent_root, project_root, document, or server_alias.",
    ),
    path_or_alias: str = typer.Option(
        ...,
        "--path-or-alias",
        help="Filesystem path (agent_root/project_root/document) or server alias.",
    ),
    project_name: str | None = typer.Option(
        None,
        "--project-name",
        help="Human-readable project name (optional).",
    ),
    discovered_from: list[str] | None = typer.Option(
        None,
        "--discovered-from",
        help="Agent root label(s) this source was discovered from. Repeatable.",
    ),
    confidence: float = typer.Option(
        0.0,
        "--confidence",
        min=0.0,
        help="Discovery confidence score.",
    ),
    evidence_count: int = typer.Option(
        0,
        "--evidence-count",
        min=0,
        help="Number of discovery evidence items.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    json_output: bool = typer.Option(False, "--json", help="Print result as JSON."),
) -> None:
    """Add or update a single confirmed source in the catalog.

    Computes a stable id from --source-type and --path-or-alias, then
    inserts or replaces the matching entry.  Existing entries with
    different ids are preserved unchanged.
    """
    if source_type not in _VALID_SOURCE_TYPES:
        valid = ", ".join(sorted(_VALID_SOURCE_TYPES))
        raise typer.BadParameter(f"invalid source-type {source_type!r}; expected one of {valid}")

    catalog_path = catalog if catalog is not None else default_catalog_path(home)
    sc = SourceCatalog.load(catalog_path)

    entry = CatalogEntry(
        id=source_id(source_type, path_or_alias),
        source_type=source_type,
        path_or_alias=path_or_alias,
        project_name=project_name,
        discovered_from=list(discovered_from or []),
        confirmation_status="confirmed",
        privacy_scope="local-private",
        confidence=confidence,
        evidence_count=evidence_count,
    )

    action, _ = sc.upsert_entry(entry)
    total = len(sc.entries)

    if not yes:
        verb = "Update" if action == "updated" else "Add"
        desc = f"{entry.source_type}:{entry.path_or_alias}"
        if entry.project_name:
            desc += f" [{entry.project_name}]"
        try:
            typer.echo(f"{verb} {desc} in {catalog_path}? [y/N] ", nl=False, err=json_output)
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            typer.echo("Aborted.", err=json_output)
            raise typer.Exit(code=1)
        if answer not in ("y", "yes"):
            typer.echo("Aborted.", err=json_output)
            raise typer.Exit(code=1)

    sc.save(catalog_path)
    if json_output:
        typer.echo(json.dumps({
            "action": action,
            "entry": asdict(entry),
            "catalog_path": str(catalog_path),
            "total_entries": total,
        }, indent=2, sort_keys=True))
        return

    typer.echo(f"{'Updated' if action == 'updated' else 'Added'}: "
               f"{entry.source_type}:{entry.path_or_alias} "
               f"to {catalog_path} (total: {total} entries)")


@sources_app.command("health")
def sources_health(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Path to the source catalog JSON file (default: <home>/.truenex-memory/sources.json).",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        min=1,
        max=500,
        help="Max action rows to show.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
) -> None:
    """Review source catalog and ledger health without writing anything."""
    catalog_path = catalog if catalog is not None else default_catalog_path(home)
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    report = build_source_health(catalog_path, db_path, apply=False, limit=limit)

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_source_health_report(report))


@sources_app.command("cleanup")
def sources_cleanup(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Path to the source catalog JSON file (default: <home>/.truenex-memory/sources.json).",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply cleanup changes."),
    limit: int = typer.Option(
        50,
        "--limit",
        min=1,
        max=500,
        help="Max action rows to show.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
) -> None:
    """Clean source catalog/ledger health issues.

    Dry-run by default.  With --yes, missing local catalog entries are disabled
    and expected ledger problems are marked skipped.  No indexed chunks or
    memory nodes are deleted.
    """
    catalog_path = catalog if catalog is not None else default_catalog_path(home)
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    report = build_source_health(catalog_path, db_path, apply=yes, limit=limit)

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_source_health_report(report))
        if not yes:
            typer.echo("\n(dry-run, pass --yes to apply cleanup)")


@sources_app.command("purge-missing")
def sources_purge_missing(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply the purge (delete rows)."),
    path_filter: list[str] | None = typer.Option(
        None,
        "--path-filter",
        help="Case-insensitive substring filter on source paths; repeatable. "
        "Only missing entries matching at least one filter are purged.",
    ),
    sample: int = typer.Option(
        10,
        "--sample",
        min=0,
        max=100,
        help="Max sample paths to show in dry-run.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
) -> None:
    """Permanently delete ledger entries marked `missing` and their content.

    Dry-run by default.  With --yes, deletes source_ledger rows with
    status 'missing', plus the documents whose path matches a purged
    source_path_or_alias and all their chunks (FTS rows are removed by the
    chunks delete trigger).  Documents whose path is still referenced by a
    non-missing ledger row are never deleted.  Unlike `cleanup`, this
    command frees disk space and removes stale evidence from search.
    Note: future reindexing skips any directory named `worktrees` at any
    depth (see core/exclusions.py), so purged worktree copies stay out.
    """
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    report = purge_missing_ledger_entries(
        db_path,
        apply=yes,
        path_filters=path_filter,
        sample_limit=sample,
    )

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_ledger_purge_report(report))
        if not yes:
            typer.echo("\n(dry-run, pass --yes to apply the purge)")


def _filter_catalog_entries(
    entries: list[object],
    *,
    include: list[str] | None,
    exclude: list[str] | None,
    source_type: list[str] | None,
) -> list[object]:
    """Filter catalog entries with case-insensitive CLI semantics.

    Repeated includes keep entries matching any term. Repeated excludes drop
    entries matching any term, so exclude wins when an entry matches both.
    Source types must match exactly after lowercasing.
    """
    includes = [item.lower() for item in (include or []) if item.strip()]
    excludes = [item.lower() for item in (exclude or []) if item.strip()]
    source_types = {item.lower() for item in (source_type or []) if item.strip()}
    if not includes and not excludes and not source_types:
        return entries

    filtered: list[object] = []
    for entry in entries:
        haystack = _catalog_entry_search_text(entry)
        entry_source_type = str(getattr(entry, "source_type", "")).lower()
        if source_types and entry_source_type not in source_types:
            continue
        if includes and not any(term in haystack for term in includes):
            continue
        if excludes and any(term in haystack for term in excludes):
            continue
        filtered.append(entry)
    return filtered


def _catalog_entry_search_text(entry: object) -> str:
    parts = [
        getattr(entry, "id", ""),
        getattr(entry, "source_type", ""),
        getattr(entry, "path_or_alias", ""),
        getattr(entry, "project_name", "") or "",
        getattr(entry, "privacy_scope", ""),
    ]
    discovered = getattr(entry, "discovered_from", [])
    if isinstance(discovered, list):
        parts.extend(str(item) for item in discovered)
    return " ".join(str(part) for part in parts).lower()


@global_app.command("refresh")
def global_refresh(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Path to the source catalog JSON file (default: <home>/.truenex-memory/sources.json).",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report planned actions without modifying DB/ledger.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
    detail_limit: int = typer.Option(
        200,
        "--detail-limit",
        min=0,
        help="Maximum per-source detail rows in JSON output; use 0 for no details.",
    ),
    full_details: bool = typer.Option(
        False,
        "--full-details",
        help="Include all per-source detail rows in JSON output.",
    ),
    stability_seconds: int = typer.Option(
        120,
        "--stability-seconds",
        min=0,
        help="Skip .jsonl files modified within this many seconds (default 120).",
    ),
) -> None:
    """Run incremental global refresh from confirmed source catalog.

    Loads confirmed sources from the catalog, runs parsers, checks the
    source ledger for changes, and indexes only new or modified content.
    """
    catalog_path = catalog if catalog is not None else default_catalog_path(home)
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"

    if not catalog_path.exists():
        if json_output:
            typer.echo(
                json.dumps(
                    {"error": f"Catalog file not found: {catalog_path}"},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            typer.echo(f"Error: Catalog file not found: {catalog_path}")
        raise typer.Exit(code=1)

    report = run_global_refresh(
        catalog_path=catalog_path,
        db_path=db_path,
        dry_run=dry_run,
        stability_seconds=stability_seconds,
    )
    if json_output:
        limit = None if full_details else detail_limit
        typer.echo(json.dumps(report.to_dict(detail_limit=limit), indent=2, sort_keys=True))
    else:
        typer.echo(format_refresh_report(report))
        if dry_run:
            typer.echo("\n(dry-run, DB/ledger unchanged)")


@global_app.command("status")
def global_status(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Path to the source catalog JSON file (default: <home>/.truenex-memory/sources.json).",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
) -> None:
    """Show read-only global store status (catalog, ledger, indexed, problems).

    This command never creates directories, databases, catalog files, or
    ledger rows.  It only reports on what already exists.
    """
    catalog_path = catalog if catalog is not None else default_catalog_path(home)
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"

    report = build_global_status(catalog_path=catalog_path, db_path=db_path)

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_status_report(report))


@global_app.command("context")
def global_context(
    project: str = typer.Argument(..., help="Project name, basename, or path alias to look up."),
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Path to the source catalog JSON file (default: <home>/.truenex-memory/sources.json).",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Max source/chunk excerpts (default 20).",
    ),
) -> None:
    """Show read-only context for a confirmed project from the global store.

    Resolves the project from the confirmed source catalog and reads the
    SQLite DB/ledger/index without mutating anything.  Server aliases are
    reported as hints only and never executed.

    This command never creates directories, databases, catalog files, or
    ledger rows.
    """
    catalog_path = catalog if catalog is not None else default_catalog_path(home)
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"

    report = build_project_context(
        project_query=project,
        catalog_path=catalog_path,
        db_path=db_path,
        limit=limit,
    )

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_context_report(report))


@global_app.command("search")
def global_search(
    query: str = typer.Argument(..., help="Search query for the global store."),
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    top_k: int = typer.Option(
        DEFAULT_GLOBAL_SEARCH_LIMIT,
        "--top-k",
        min=1,
        max=50,
        help="Maximum global search results.",
    ),
    kind: str = typer.Option(
        "all",
        "--kind",
        help="Search result kind: all, memory, or chunks.",
    ),
    include_inactive: bool = typer.Option(
        False,
        "--include-inactive",
        help="Include inactive memory statuses such as obsolete or superseded.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
) -> None:
    """Search the global store without mutating retrieval logs or DB state.

    Result scores are Reciprocal Rank Fusion scores on a single small
    positive scale (max ~0.041): memory nodes and document chunks are
    ranked independently and merged by position, so curated memories are
    never buried by raw BM25 score magnitude.
    """
    if kind not in GLOBAL_SEARCH_KINDS:
        expected = ", ".join(sorted(GLOBAL_SEARCH_KINDS))
        raise typer.BadParameter(f"invalid kind {kind!r}; expected one of {expected}")
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    report = build_global_search(
        db_path=db_path,
        query=query,
        top_k=top_k,
        include_inactive=include_inactive,
        kind_filter=kind,
    )

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_global_search_report(report))


@global_app.command("reindex-embeddings")
def global_reindex_embeddings(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Process at most N chunks (for tests and partial runs).",
    ),
    batch_size: int = typer.Option(
        DEFAULT_REINDEX_BATCH_SIZE,
        "--batch-size",
        min=1,
        help="Chunks embedded and committed per batch.",
    ),
    device: str | None = typer.Option(
        None,
        "--device",
        help="Torch device for the embedder (default: cuda if available, else cpu).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Actually run the re-embedding. Default is a dry-run that only counts.",
    ),
) -> None:
    """Re-embed chunks with the semantic embedder (intfloat/multilingual-e5-base).

    Resumable: chunks already carrying the active embedding_model are
    skipped and each batch is committed separately, so interrupting and
    re-launching continues from where the previous run stopped. Without
    --yes it only prints the counts (to reindex / already current).

    Operational note: every committed batch bumps chunks.updated_at, which
    invalidates the dense vector index cache. With the dense ranker active
    (TRUENEX_EMBEDDER=e5 and TRUENEX_DENSE not off), any search during the
    reindex window pays a full matrix reload (~45s per query on the live
    store, measured). Recommended: run the reindex in a quiet window or
    with TRUENEX_DENSE=off.

    The final performance re-measurement (median/p95, dense ON vs OFF on
    the full live store) will be run post-reindex via
    scripts/eval_retrieval.py — Kimi owns that step.
    """
    from truenex_memory.core.embedder import (
        SentenceTransformerEmbedder,
        sentence_transformers_model_name,
    )
    from truenex_memory.ingestion.reindex_embeddings import ModelNameOnlyEmbedder

    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    if not db_path.exists():
        raise typer.BadParameter(f"database not found: {db_path}")

    if not yes:
        # Dry-run: count only — resolve the persisted model name WITHOUT
        # instantiating the model (no ~1.1GB download for two counters).
        embedder: ModelNameOnlyEmbedder | SentenceTransformerEmbedder = (
            ModelNameOnlyEmbedder(sentence_transformers_model_name())
        )
    else:
        embedder = SentenceTransformerEmbedder(device=device)
    report = reindex_embeddings(
        db_path,
        embedder=embedder,
        batch_size=batch_size,
        limit=limit,
        dry_run=not yes,
        device=getattr(embedder, "device", None) if yes else None,
    )
    summary = report.to_dict()
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
    if report.dry_run:
        typer.echo(
            f"\nDry-run: {report.to_reindex} chunks to re-embed "
            f"({report.already_current} already current). Re-run with --yes to execute."
        )
    else:
        typer.echo(
            f"\nProcessed {report.processed} chunks in {report.elapsed_s:.1f}s "
            f"({report.chunks_per_second} chunks/s) on device {report.device}."
        )
        if report.errors:
            typer.echo("Errors (run is resumable, re-launch to continue):")
            for error in report.errors:
                typer.echo(f"  - {error}")


@auto_app.command("run")
def auto_run(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Path to the source catalog JSON file (default: <home>/.truenex-memory/sources.json).",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report planned actions without modifying DB/ledger.",
    ),
    skip_refresh: bool = typer.Option(
        False,
        "--skip-refresh",
        help="Use existing indexed DB only; do not parse catalog or source files.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
    detail_limit: int = typer.Option(
        200,
        "--detail-limit",
        min=0,
        help="Maximum per-source detail rows in JSON output; use 0 for no details.",
    ),
    full_details: bool = typer.Option(
        False,
        "--full-details",
        help="Include all per-source detail rows in JSON output.",
    ),
    stability_seconds: int = typer.Option(
        120,
        "--stability-seconds",
        min=0,
        help="Skip .jsonl files modified within this many seconds (default 120).",
    ),
    auto_memory: bool = typer.Option(
        False,
        "--auto-memory",
        help="Generate exact-deduped unverified memory nodes after refresh.",
    ),
    min_confidence: float = typer.Option(
        DEFAULT_CONFIDENCE,
        "--min-confidence",
        min=0.0,
        max=1.0,
        help="Minimum confidence for generated unverified memory nodes.",
    ),
    auto_memory_limit: int = typer.Option(
        DEFAULT_AUTO_MEMORY_LIMIT,
        "--auto-memory-limit",
        min=0,
        help="Maximum generated memory nodes per run; 0 means unlimited.",
    ),
    auto_memory_per_source_limit: int = typer.Option(
        DEFAULT_AUTO_MEMORY_PER_SOURCE_LIMIT,
        "--auto-memory-per-source-limit",
        min=0,
        help="Maximum generated memory nodes per source path per run; 0 means unlimited.",
    ),
) -> None:
    """Run automatic memory refresh (Phase 3 daily-use wrapper over global refresh).

    This command mirrors 'global refresh' for Phase 3.1.  No generated memory
    nodes, watcher, persistent config, or MCP changes are active yet.
    """
    check_license("pro")
    catalog_path = catalog if catalog is not None else default_catalog_path(home)
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"

    if skip_refresh and not auto_memory:
        _print_auto_run_error(
            "--skip-refresh requires --auto-memory",
            json_output=json_output,
            exit_code=1,
        )

    if skip_refresh and not db_path.exists():
        _print_auto_run_error(
            f"Database file not found: {db_path}",
            json_output=json_output,
            exit_code=1,
        )

    if not skip_refresh and not catalog_path.exists():
        if json_output:
            typer.echo(
                json.dumps(
                    {"error": f"Catalog file not found: {catalog_path}"},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            typer.echo(f"Error: Catalog file not found: {catalog_path}")
        raise typer.Exit(code=1)

    if skip_refresh:
        report = RefreshReport(refresh_skipped=True)
    else:
        report = run_global_refresh(
            catalog_path=catalog_path,
            db_path=db_path,
            dry_run=dry_run,
            stability_seconds=stability_seconds,
        )
    if auto_memory:
        generate_unverified_auto_memories(
            db_path,
            report,
            dry_run=dry_run,
            min_confidence=min_confidence,
            limit=auto_memory_limit,
            per_source_limit=auto_memory_per_source_limit,
        )

    if json_output:
        limit = None if full_details else detail_limit
        typer.echo(json.dumps(report.to_dict(detail_limit=limit), indent=2, sort_keys=True))
    else:
        typer.echo(format_refresh_report(report))
        if dry_run:
            typer.echo("\n(dry-run, DB/ledger unchanged)")


def _print_auto_run_error(message: str, *, json_output: bool, exit_code: int) -> None:
    if json_output:
        typer.echo(json.dumps({"error": message}, indent=2, sort_keys=True))
    else:
        typer.echo(f"Error: {message}")
    raise typer.Exit(code=exit_code)


@auto_app.command("status")
def auto_status(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    catalog: Path | None = typer.Option(
        None,
        "--catalog",
        help="Path to the source catalog JSON file (default: <home>/.truenex-memory/sources.json).",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to the SQLite database (default: <home>/.truenex-memory/truenex_memory.db).",
    ),
    stability_seconds: int = typer.Option(
        120,
        "--stability-seconds",
        min=0,
        help="Treat recent unstable .jsonl sessions as transient within this many seconds.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
) -> None:
    """Show read-only automatic memory status (Phase 3.2)."""
    check_license("pro")
    catalog_path = catalog if catalog is not None else default_catalog_path(home)
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"

    report = build_auto_status(
        catalog_path=catalog_path,
        db_path=db_path,
        stability_seconds=stability_seconds,
    )

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_auto_status_report(report))


@auto_app.command("review")
def auto_review(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to global memory database.",
    ),
    limit: int = typer.Option(
        DEFAULT_REVIEW_LIMIT,
        "--limit",
        min=1,
        help="Maximum generated memory nodes to display.",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Case-insensitive substring filter for source_path.",
    ),
    content_chars: int = typer.Option(
        DEFAULT_CONTENT_CHARS,
        "--content-chars",
        min=40,
        help="Maximum characters shown for each text excerpt.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Review generated unverified auto memories without mutating the store."""
    check_license("pro")
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    report = build_auto_memory_review(
        db_path=db_path,
        limit=limit,
        source_filter=source,
        content_chars=content_chars,
    )
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_auto_memory_review(report))


@auto_app.command("approve")
def auto_approve(
    memory_id: str = typer.Argument(..., help="Generated unverified auto-memory id."),
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to global memory database.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Promote one generated unverified auto memory to active."""
    check_license("pro")
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    report = approve_auto_memory(db_path, memory_id)
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_auto_memory_lifecycle_report(report))


@auto_app.command("reject")
def auto_reject(
    memory_id: str = typer.Argument(..., help="Generated unverified auto-memory id."),
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to global memory database.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Mark one generated unverified auto memory obsolete without deleting it."""
    check_license("pro")
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    report = reject_auto_memory(db_path, memory_id)
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_auto_memory_lifecycle_report(report))


@auto_app.command("promote")
def auto_promote(
    memory_id: str = typer.Argument(..., help="Generated unverified auto-memory id."),
    title: str = typer.Option(
        ...,
        "--title",
        help="Curated title for the new active memory.",
    ),
    content: str = typer.Option(
        ...,
        "--content",
        help="Curated content for the new active memory.",
    ),
    memory_type: str = typer.Option(
        "note",
        "--type",
        help="Curated memory type: note, decision, issue, or pattern.",
    ),
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to global memory database.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate and show the planned curated replacement without writing.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Create a curated active memory from one noisy unverified auto memory."""
    check_license("pro")
    if memory_type not in CURATED_AUTO_MEMORY_TYPES:
        expected = ", ".join(sorted(CURATED_AUTO_MEMORY_TYPES))
        raise typer.BadParameter(
            f"invalid memory type {memory_type!r}; expected one of {expected}",
            param_hint="'--type'",
        )
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    try:
        report = promote_auto_memory(
            db_path,
            memory_id,
            title=title,
            content=content,
            memory_type=memory_type,
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_auto_memory_lifecycle_report(report))


@auto_app.command("prune")
def auto_prune(
    home: Path = typer.Option(
        Path.home(),
        "--home",
        help="User home directory for default paths.",
    ),
    db: Path | None = typer.Option(
        None,
        "--db",
        help="Path to global memory database.",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Case-insensitive substring filter for source_path.",
    ),
    limit: int = typer.Option(
        DEFAULT_PRUNE_LIMIT,
        "--limit",
        min=1,
        help="Maximum rejected auto memories to compact.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Apply compaction. Without this flag the command is a dry-run.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Compact rejected auto memories into tombstones; dry-run by default."""
    check_license("pro")
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    report = prune_auto_memories(
        db_path,
        source_filter=source,
        limit=limit,
        dry_run=not yes,
    )
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(format_auto_memory_lifecycle_report(report))


def _print_ingest_report(report: dict[str, object], dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "INGEST"
    typer.echo(f"=== {mode} REPORT ===")

    index_now = report.get("index_now", [])
    if isinstance(index_now, list) and index_now:
        typer.echo(f"\nIndex now ({len(index_now)}):")
        for item in index_now:
            if isinstance(item, dict):
                sid = item.get("session_id", "")
                sid_str = f" session={sid}" if sid else ""
                typer.echo(
                    f"  [{item.get('source_type', '?')}] {item.get('source_path', '?')}"
                    f" ({item.get('chars', 0)} chars){sid_str}"
                )

    parse_later = report.get("parse_later", [])
    if isinstance(parse_later, list) and parse_later:
        typer.echo(f"\nParse later ({len(parse_later)}):")
        for item in parse_later:
            if isinstance(item, dict):
                typer.echo(f"  [{item.get('source_type', '?')}] {item.get('source_path', '?')}")

    skipped = report.get("skipped", [])
    if isinstance(skipped, list) and skipped:
        typer.echo(f"\nSkipped ({len(skipped)}):")
        for item in skipped:
            if isinstance(item, dict):
                typer.echo(
                    f"  [{item.get('source_type', '?')}] {item.get('source_path', '?')}"
                    f" - {item.get('reason', '?')}"
                )

    errors = report.get("errors", [])
    if isinstance(errors, list) and errors:
        typer.echo(f"\nErrors ({len(errors)}):")
        for item in errors:
            if isinstance(item, dict):
                typer.echo(
                    f"  [{item.get('source_type', '?')}] {item.get('source_path', '?')}"
                    f" - {item.get('error', '?')}"
                )

    total = (
        (len(index_now) if isinstance(index_now, list) else 0)
        + (len(parse_later) if isinstance(parse_later, list) else 0)
        + (len(skipped) if isinstance(skipped, list) else 0)
        + (len(errors) if isinstance(errors, list) else 0)
    )
    suffix = " (dry-run, DB unchanged)" if dry_run else ""
    typer.echo(f"\nTotal: {total} sources{suffix}")


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        expected = ", ".join(sorted(VALID_STATUSES))
        raise typer.BadParameter(f"invalid status {status!r}; expected one of {expected}")


# ── Code graph ─────────────────────────────────────────────────────────────

def _graph_cache_dir(db: Path | None, home: Path) -> Path:
    """Cache lives beside the database, like the dense vector cache does."""
    db_path = db if db is not None else home / ".truenex-memory" / "truenex_memory.db"
    return db_path.parent / "code_graphs"


def _current_graph_for(root: Path, cache_dir: Path):
    """Il grafo in cache di questa radice, se scritto dalla versione corrente.

    Un grafo di una versione precedente non porta l'impronta dei sorgenti, per
    cui non puo' dire di essere aggiornato: viene trattato come assente e
    ricostruito.
    """
    from truenex_memory.graph import CACHE_VERSION, FileGraph

    wanted = root.as_posix().lower()
    for entry in sorted(cache_dir.glob("*.json")) if cache_dir.is_dir() else []:
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("cache_version") != CACHE_VERSION:
            continue
        if str(data.get("root", "")).lower() != wanted:
            continue
        graph = FileGraph.from_dict(data)
        return graph if graph.fingerprint else None
    return None


@graph_app.command("build")
def graph_build(
    path: Path = typer.Argument(Path("."), help="Project root to extract."),
    home: Path = typer.Option(Path.home(), "--home", help="User home directory for default paths."),
    db: Path | None = typer.Option(None, "--db", help="Path to the SQLite database."),
    limit: int | None = typer.Option(None, "--limit", help="Stop after N source files (for a quick trial)."),
    sequential: bool = typer.Option(False, "--sequential", help="Disable the extraction process pool."),
    if_stale: bool = typer.Option(
        False,
        "--if-stale",
        help="Non fare nulla se il grafo e' gia' aggiornato. Da usare in un hook o in un'attivita' pianificata.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the report as JSON."),
) -> None:
    """Extract the code graph for a project and cache it for the project view.

    The graph is built here and never during an HTTP request: extraction
    takes seconds to minutes, which a GET must not pay.

    ``--if-stale`` esiste perche' «ricordati di ricostruire il grafo dopo aver
    cambiato il codice» e' un compito che non va dato a una persona. Con quel
    flag il comando confronta l'impronta dei sorgenti e esce subito se non e'
    cambiato niente (millisecondi), quindi si puo' chiamare a ogni salvataggio
    o a ogni ora senza pagare l'estrazione a vuoto.
    """
    from truenex_memory.graph import (
        GraphifyUnavailable,
        build_file_graph,
        release_lock,
        save_file_graph,
    )

    root = path.resolve()
    if not root.is_dir():
        typer.echo(f"not a directory: {root}")
        raise typer.Exit(code=1)

    if if_stale:
        cached = _current_graph_for(root, _graph_cache_dir(db, home))
        if cached is not None:
            freshness = cached.staleness()
            if freshness.get("stale") is False:
                message = {"skipped": "up to date", "root": cached.root}
                typer.echo(json.dumps(message) if json_output else "il grafo e' aggiornato, niente da fare")
                return

    try:
        graph = build_file_graph(root, limit=limit, parallel=not sequential)
    except GraphifyUnavailable as error:
        typer.echo(str(error))
        raise typer.Exit(code=1)

    cache_dir = _graph_cache_dir(db, home)
    target = save_file_graph(graph, cache_dir)
    # Il lucchetto della ricostruzione in disparte: se questo processo e' quello
    # lanciato da `ensure_current`, va liberato ora, altrimenti il prossimo
    # invecchiamento resterebbe senza rimedio fino alla scadenza.
    release_lock(cache_dir, graph.root)

    if json_output:
        typer.echo(json.dumps({"cache": str(target), **graph.to_dict()["stats"]}, indent=2))
        return

    stats = graph.stats
    typer.echo(f"root:        {graph.root}")
    typer.echo(f"file sorgente: {stats.get('files', 0)}")
    if not stats.get("files"):
        # Uno zero muto si legge come «nessuna relazione», mentre significa
        # «non ho guardato niente». Su una macchina reale un progetto di otto
        # file VB.NET ha prodotto quello zero, e la differenza fra le due
        # letture e' tutta.
        from truenex_memory.graph import unsupported_languages_seen

        non_letti = unsupported_languages_seen(stats.get("skipped_by_suffix") or {})
        if non_letti:
            elenco = ", ".join(f"{quanti} {suffisso} ({lingua})" for suffisso, lingua, quanti in non_letti[:4])
            typer.echo("")
            typer.echo(f"nessun file analizzabile, ma ce ne sono: {elenco}")
            typer.echo("  l'estrattore non ha la grammatica per questi linguaggi, quindi il grafo")
            typer.echo("  non puo' dire niente su questo progetto — non e' un progetto vuoto")
        elif stats.get("skipped_total"):
            typer.echo("")
            typer.echo(f"nessun file analizzabile su {stats['skipped_total']} scartati per estensione")
            typer.echo("  TRUENEX_GRAPH_SUFFIXES per includerne altre")
    typer.echo(f"entita:      {stats.get('entity_nodes', 0)} nodi, {stats.get('entity_edges', 0)} archi")
    typer.echo(f"archi fra file: {stats.get('file_edges', 0)}")
    # Cio' che il filtro sulle estensioni ha lasciato fuori, per estensione: un
    # file mai analizzato risponde «nessun chiamante» esattamente come un file
    # analizzato che non ne ha, e i due casi vanno distinti.
    dropped = stats.get("skipped_by_suffix") or {}
    if dropped:
        detail = ", ".join(f"{ext} {count}" for ext, count in list(dropped.items())[:6])
        typer.echo(f"non analizzati: {stats.get('skipped_total', 0)} ({detail})")
        typer.echo("               TRUENEX_GRAPH_SUFFIXES per includerne altri")
    for relation, weight in list(stats.get("relations", {}).items())[:8]:
        typer.echo(f"  {relation:16s} {weight}")
    typer.echo(f"cache:       {target}")


@graph_app.command("explain")
def graph_explain(
    target: str = typer.Argument(..., help="Function, class or file, e.g. _search_memories."),
    scope: str | None = typer.Option(None, "--scope", help="Which project's graph to consult."),
    limit: int = typer.Option(12, "--limit", min=1, max=50, help="Max entries per group."),
    home: Path = typer.Option(Path.home(), "--home", help="User home directory."),
    db: Path | None = typer.Option(None, "--db", help="Path to the SQLite database."),
    json_output: bool = typer.Option(False, "--json", help="Print as JSON."),
) -> None:
    """Who calls it, what it calls, which tests cover it, and why it exists.

    Reads the cached code graph, not the text index: the relations come from a
    parser, so they are correct or absent, never a plausible near-match. Run
    `graph build` first for the tree you are asking about.
    """
    from truenex_memory.graph import FileGraph, explain_entity

    cache_dir = _graph_cache_dir(db, home)
    graphs = sorted(cache_dir.glob("*.json")) if cache_dir.is_dir() else []
    if not graphs:
        typer.echo("nessun grafo costruito. Esegui prima: truenex-mem graph build <cartella>")
        raise typer.Exit(code=1)

    from truenex_memory.graph import CACHE_VERSION

    best = None
    stale: list[str] = []
    for entry in graphs:
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # Un grafo scritto da una versione precedente non ha il livello
        # entita', quindi risponderebbe "non trovato" a QUALUNQUE funzione. Va
        # detto, non lasciato interpretare come un'assenza.
        if data.get("cache_version") != CACHE_VERSION:
            stale.append(str(data.get("root", entry.name)))
            continue
        graph = FileGraph.from_dict(data)
        if scope and scope.replace("\\", "/").lower() not in graph.root.lower():
            continue
        if explain_entity(graph, target, limit=1)["matched"]:
            best = graph
            break
        if best is None:
            best = graph
    if best is None:
        if stale:
            typer.echo("il grafo di questi progetti e' stato costruito da una versione")
            typer.echo("precedente e non contiene le funzioni. Ricostruiscilo:")
            for root in stale:
                typer.echo(f"  truenex-mem graph build \"{root}\"")
        else:
            typer.echo("nessun grafo corrisponde a quello scope")
        raise typer.Exit(code=1)

    # Anche l'uso dalla CLI viene contato. Il contatore vedeva solo le chiamate
    # MCP, quindi un agente che eseguiva `truenex-mem graph explain` da shell
    # risultava «mai usato»: e' successo con Kimi, che aveva usato lo strumento
    # per davvero e la misura lo dava a zero. Un falso negativo in una misura
    # nata per non fidarsi delle impressioni e' il difetto peggiore che possa
    # avere. Il client si riconosce dall'albero dei processi, perche' da qui non
    # c'e' nessun handshake in cui si dichiari.
    try:
        from truenex_memory.adapters.profile import identify_client, record_tool_use

        chi, _ = identify_client(None)
        record_tool_use(chi.client if chi else "(riga di comando)", "memory_graph", {})
    except Exception:  # pragma: no cover - mai far cadere il comando
        pass

    result = explain_entity(best, target, limit=limit)
    # Un grafo invecchiato risponde sul codice di ieri senza dichiararlo, ed e'
    # indistinguibile da una risposta giusta. Il confronto costa la lettura dei
    # metadati dei sorgenti, quindi si fa sempre invece di chiedere a qualcuno
    # di ricordarsi di ricostruire.
    from truenex_memory.graph import ensure_current

    freshness = ensure_current(best, cache_dir)
    result["stale"] = freshness
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if freshness.get("stale"):
        counts = freshness["counts"]
        parts = [
            f"{counts[key]} {label}"
            for key, label in (
                ("changed", "file modificati"),
                ("missing", "file spariti"),
                ("tree", "cartelle cambiate"),
            )
            if counts[key]
        ]
        typer.echo(f"ATTENZIONE: il grafo e' piu' vecchio del codice ({', '.join(parts)}).")
        rebuild = freshness.get("rebuild")
        if rebuild == "avviata":
            typer.echo("  ricostruzione avviata in disparte: la prossima domanda avra' il grafo nuovo")
        elif rebuild == "in corso":
            typer.echo("  ricostruzione gia' in corso da un'altra sessione")
        else:
            typer.echo(f"  ricostruisci: truenex-mem graph build \"{best.root}\"")
        typer.echo("")

    if not result["matched"]:
        typer.echo(f"'{target}' non compare nel grafo di {best.root}")
        raise typer.Exit(code=1)

    copertura = result.get("coverage") or {}
    # I codici compatti della risposta tornano frasi: nella CLI le legge una
    # persona una volta sola, quindi la brevita' non serve a nessuno.
    spiegazioni = {
        "cross_file_method_calls": (
            "su questo linguaggio le chiamate a metodo attraverso un ricevitore da un "
            "altro file non sempre vengono agganciate"
        ),
        "no_caller_outside_defining_file": (
            "nessun chiamante fuori dal file di definizione: e' la forma tipica di "
            "un'estrazione incompleta, non una prova che non ce ne siano"
        ),
    }
    for avviso in copertura.get("incomplete", []):
        codice = avviso.split(":", 1)[0]
        dettaglio = avviso.split(":", 1)[1].strip() if ":" in avviso else ""
        typer.echo(f"ATTENZIONE: {spiegazioni.get(codice, codice)}")
        if dettaglio:
            typer.echo(f"            misurato: {dettaglio}")
    if copertura.get("incomplete"):
        typer.echo("")
    typer.echo(f"progetto: {best.root}")
    for name in result["matched"]:
        typer.echo(f"trovato : {name}")
    for label, key in (
        ("CHI LO CHIAMA", "callers"),
        ("COSA CHIAMA", "calls"),
        ("TEST CHE LO COPRONO", "tests"),
    ):
        typer.echo("")
        typer.echo(f"{label}:")
        if not result[key]:
            typer.echo("  (nessuno)")
        for item in result[key]:
            # La provenienza viaggia accanto al dato: entrambe le review hanno
            # insistito, perche' altrimenti chi legge tratta una deduzione come
            # una lettura.
            marchio = " (dedotto dal tipo)" if item.get("confidence") == "inferred" else ""
            typer.echo(f"  {item['entity']}  [{item['relation']}]{marchio}")
        hidden = result.get("truncated", {}).get(key)
        if hidden:
            typer.echo(f"  ... mostrati {len(result[key])} di {hidden} — tutti con --limit {hidden}")
    if copertura.get("incomplete") and result["matched"]:
        from truenex_memory.graph import text_call_sites

        nome = result["matched"][0].split("::", 1)[-1].lstrip(".")
        candidati = text_call_sites(Path(best.root), nome, files=best.file_set())
        noti = {c["entity"].split("::", 1)[0] for c in result["callers"]}
        fuori = [c for c in candidati if c["file"] not in noti]
        if fuori:
            typer.echo("")
            typer.echo("CANDIDATI DA RICERCA TESTUALE (non risolti dal parser, da verificare):")
            for c in fuori:
                typer.echo(f"  {c['file']}:{c['line']}  {c['text'][:80]}")

    if copertura.get("tests_detection") and not result["tests"]:
        typer.echo(
            "  (il vuoto qui non e' un «nessuno»: i test di questo linguaggio stanno "
            "nello stesso file e il nome non li tradisce, quindi significa «non lo so»)"
        )
    if result["rationale"]:
        typer.echo("")
        typer.echo("PERCHE' ESISTE (dal docstring):")
        for text in result["rationale"]:
            typer.echo(f"  {text[:150]}")


@graph_app.command("status")
def graph_status(
    home: Path = typer.Option(Path.home(), "--home", help="User home directory for default paths."),
    db: Path | None = typer.Option(None, "--db", help="Path to the SQLite database."),
    json_output: bool = typer.Option(False, "--json", help="Print the report as JSON."),
) -> None:
    """List the cached code graphs, without building anything."""
    from truenex_memory.graph import FileGraph, graphify_available

    cache_dir = _graph_cache_dir(db, home)
    entries = []
    for entry in sorted(cache_dir.glob("*.json")) if cache_dir.is_dir() else []:
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        graph = FileGraph.from_dict(data)
        entries.append({"root": graph.root, "cache": str(entry), **graph.stats})

    if json_output:
        typer.echo(json.dumps({"backend_installed": graphify_available(), "graphs": entries}, indent=2))
        return

    typer.echo(f"backend di estrazione: {'presente' if graphify_available() else 'assente (pip install truenex-memory[graph])'}")
    typer.echo(f"cache: {cache_dir}")
    if not entries:
        typer.echo("nessun grafo costruito")
        return
    for item in entries:
        typer.echo(f"  {item['root']}")
        typer.echo(f"    {item.get('files', 0)} file, {item.get('file_edges', 0)} archi fra file")


if __name__ == "__main__":
    app()


# ── profilo di comportamento ──────────────────────────────────────────────

@profile_app.command("show")
def profile_show(
    raw: bool = typer.Option(False, "--raw", help="Solo il testo, senza i marcatori."),
) -> None:
    """Mostra il blocco che memory scrive nei file dei client.

    Sola lettura: non tocca nessun file. Serve a vedere cosa verrebbe scritto
    prima di scriverlo — e a leggere cosa un agente riceve oggi.
    """
    from truenex_memory.adapters.profile import PROFILE_VERSION, profile_text, render_block

    typer.echo(profile_text() if raw else render_block())
    if not raw:
        typer.echo("")
        typer.echo(f"versione {PROFILE_VERSION}, {len(profile_text().split())} parole")


@profile_app.command("status")
def profile_status(
    home: Path | None = typer.Option(None, "--home", help="Cartella utente (per i test)."),
    json_output: bool = typer.Option(False, "--json", help="Stampa il rapporto come JSON."),
) -> None:
    """Quali client hanno il profilo, quali no, e chi e' rimasto indietro.

    Sola lettura. E' la fotografia che rende visibile la deriva: senza questa,
    un client con il profilo di tre versioni fa e' indistinguibile da uno
    aggiornato.
    """
    from truenex_memory.adapters.profile import profile_home

    home = profile_home() if home is None else home
    from truenex_memory.adapters.profile import PROFILE_VERSION, status

    reports = status(home)
    if json_output:
        typer.echo(json.dumps([r.to_dict() for r in reports], indent=2, ensure_ascii=False))
        return

    etichette = {
        "unchanged": "aggiornato",
        "updated": "da aggiornare",
        "absent": "manca",
        "client-not-installed": "non installato",
        "no-file-channel": "solo handshake",
    }
    righe = [
        (
            r.client,
            etichette[r.action],
            f"v{r.present_version}" if r.present_version is not None else "—",
            str(r.path) if r.path else "— (istruzioni solo via MCP)",
        )
        for r in reports
    ]
    intestazione = ("CLIENT", "PROFILO", "VER", "FILE")
    larghezze = [max(len(x[i]) for x in [intestazione, *righe]) for i in range(4)]

    def _riga(valori):
        return "  ".join(v.ljust(w) for v, w in zip(valori, larghezze)).rstrip()

    typer.echo(f"profilo corrente: versione {PROFILE_VERSION}")
    typer.echo("")
    typer.echo("  " + _riga(intestazione))
    typer.echo("  " + "  ".join("-" * w for w in larghezze))
    for riga in righe:
        typer.echo("  " + _riga(riga))
    da_fare = [r for r in reports if r.action in {"updated", "absent"}]
    typer.echo("")
    if da_fare:
        typer.echo(f"{len(da_fare)} da sistemare: truenex-mem profile apply")
    else:
        typer.echo("tutti i client installati hanno il profilo corrente")


@profile_app.command("apply")
def profile_apply(
    home: Path | None = typer.Option(None, "--home", help="Cartella utente (per i test)."),
    project: list[Path] = typer.Option(
        [], "--project", help="Scrive il blocco anche nell'AGENTS.md di questo progetto."
    ),
    known_projects: bool = typer.Option(
        False, "--known-projects", help="Tutti i progetti di cui memory conosce il grafo."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dice cosa farebbe senza scrivere."),
    json_output: bool = typer.Option(False, "--json", help="Stampa il rapporto come JSON."),
) -> None:
    """Scrive il profilo nel file utente di ogni client installato.

    Idempotente: rieseguirlo su un sistema aggiornato non cambia niente. Tocca
    solo cio' che sta fra i marcatori; tutto il resto di quei file e' di chi li
    ha scritti e non viene sfiorato.
    """
    from truenex_memory.adapters.profile import (
        TargetReport,
        apply_all,
        apply_to_project,
        known_project_roots,
        profile_home,
        project_target,
        status,
    )

    home = profile_home() if home is None else home
    reports = status(home) if dry_run else apply_all(home)

    # Il livello di progetto: l'unico dove `AGENTS.md` sia davvero uno standard,
    # e la sola strada per i client che non leggono nessun file utente.
    radici = [Path(r) for r in project]
    if known_projects:
        radici += [r for r in known_project_roots(home) if r not in radici]
    for radice in radici:
        if dry_run:
            percorso = project_target(radice)
            try:
                presente = "truenex-memory:begin" in percorso.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                presente = False
            reports.append(
                TargetReport(
                    f"progetto {radice.name}",
                    percorso,
                    True,
                    "unchanged" if presente else "absent",
                )
            )
        else:
            reports.append(apply_to_project(radice))
    if json_output:
        typer.echo(json.dumps([r.to_dict() for r in reports], indent=2, ensure_ascii=False))
        return

    verbi = {
        "created": "scritto",
        "updated": "aggiornato",
        "unchanged": "invariato",
        "absent": "da scrivere" if dry_run else "non scritto",
        "client-not-installed": "saltato",
        "no-file-channel": "nessun file da scrivere",
    }
    righe = [
        (r.client, verbi[r.action], str(r.path) if r.path else "— (istruzioni solo via MCP)")
        for r in reports
    ]
    intestazione = ("CLIENT", "ESITO", "FILE")
    larghezze = [max(len(x[i]) for x in [intestazione, *righe]) for i in range(3)]

    def _riga(valori):
        return "  ".join(v.ljust(w) for v, w in zip(valori, larghezze)).rstrip()

    typer.echo("  " + _riga(intestazione))
    typer.echo("  " + "  ".join("-" * w for w in larghezze))
    for riga in righe:
        typer.echo("  " + _riga(riga))
    scritti = [r for r in reports if r.action in {"created", "updated"}]
    installati = [r for r in reports if r.installed]
    typer.echo("")
    if dry_run:
        typer.echo("prova a vuoto: nessun file toccato")
    else:
        typer.echo(f"{len(scritti)} file scritti, {len(installati)} client installati")


@profile_app.command("clients")
def profile_clients(
    home: Path | None = typer.Option(None, "--home", help="Cartella utente (per i test)."),
    json_output: bool = typer.Option(False, "--json", help="Stampa il rapporto come JSON."),
) -> None:
    """Chi si e' collegato davvero a memory, come si e' presentato.

    Diverso da `profile status`, che guarda quali cartelle esistono: qui ci
    sono i client che hanno aperto una sessione MCP e hanno dichiarato il
    proprio nome nell'handshake. E' il segnale autorevole, e include i nomi che
    ancora non sappiamo riconoscere — che altrimenti non scopriremmo mai,
    perche' un client senza istruzioni non da' nessun errore.
    """
    from truenex_memory.adapters.profile import profile_home

    home = profile_home() if home is None else home
    registry = home / ".truenex-memory" / "clients.json"
    try:
        known = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        typer.echo("nessun client si e' ancora collegato (o il registro non esiste)")
        typer.echo(f"  atteso in: {registry}")
        return

    if json_output:
        typer.echo(json.dumps(known, indent=2, ensure_ascii=False))
        return

    from truenex_memory.adapters.profile import identify_from_entry

    righe = []
    for name, entry in sorted(known.items()):
        # Ricalcolato, non letto dal registro: le etichette cambiano, e il
        # riconoscimento usa due segnali perche' il nome dichiarato spesso non
        # identifica niente (Kimi si presenta come `mcp`, il predefinito della
        # libreria MCP).
        target, segnale, deciso = identify_from_entry(name, entry)
        righe.append(
            (
                name[:26],
                (entry.get("version") or "—")[:18],
                str(entry.get("connections", 0)),
                # Quando il riconoscimento viene dal processo si mostra QUELLO
                # che ha deciso; per un client ignoto serve invece vedere la
                # catena, perche' e' da lì che si ricava come mapparlo.
                # Chi ha deciso quando l'ha deciso un processo; per un client
                # riconosciuto dal nome la catena e' rumore. Se non e'
                # riconosciuto serve invece vederla, perche' e' da lì che si
                # ricava come mapparlo.
                (
                    deciso
                    if segnale == "process"
                    else "—"
                    if segnale == "declared"
                    else (" > ".join((entry.get("ancestry") or [])[:3]) or entry.get("process") or "—")
                )[:40],
                target.client if target else "—",
                {"declared": "nome", "process": "processo", "none": "ignoto"}[segnale],
            )
        )

    typer.echo(f"registro: {registry}")
    typer.echo("")
    intestazione = ("CLIENT", "VERSIONE", "CONN", "PROCESSO", "RICONOSCIUTO", "DA")
    larghezze = [max(len(r[i]) for r in [intestazione, *righe]) for i in range(6)]
    def _riga(valori, intestazione=False):
        return "  ".join(
            v.rjust(w) if (i == 2 and not intestazione) else v.ljust(w)
            for i, (v, w) in enumerate(zip(valori, larghezze))
        ).rstrip()

    typer.echo("  " + _riga(intestazione, intestazione=True))
    typer.echo("  " + "  ".join("-" * w for w in larghezze))
    for riga in righe:
        typer.echo("  " + _riga(riga))

    ignoti = [r[0] for r in righe if r[5] == "ignoto"]
    senza_processo = [r[0] for r in righe if r[3] == "—"]
    if ignoti:
        typer.echo("")
        typer.echo(f"non riconosciuti: {', '.join(ignoti)}")
    if senza_processo:
        typer.echo("")
        typer.echo(
            "il processo padre manca per le connessioni avvenute prima che venisse "
            "registrato: si riempie da solo alla prossima."
        )


@profile_app.command("check")
def profile_check(
    home: Path | None = typer.Option(None, "--home", help="Cartella utente (per i test)."),
    json_output: bool = typer.Option(False, "--json", help="Stampa il rapporto come JSON."),
) -> None:
    """Il profilo e' arrivato davvero? Lo dice il comportamento, non una promessa.

    Sapere che un client si e' collegato non dice se ha letto le istruzioni, e
    chiedere all'agente di confermarlo dipenderebbe dalla sua collaborazione —
    cioe' misurerebbe la stessa cosa che si vuole verificare. Qui si guarda cio'
    che il server osserva comunque: chi ha ricevuto il profilo cerca prima di
    leggere, passa lo `scope`, usa il grafo e registra i passi.

    I numeri sono tassi, non sentenze: una ricerca senza `scope` e' legittima
    per una domanda trasversale, quindi un valore basso e' un indizio.
    """
    from truenex_memory.adapters.profile import compliance, profile_home

    home = profile_home() if home is None else home
    rapporti = compliance(home / ".truenex-memory" / "clients.json")
    if json_output:
        typer.echo(json.dumps(rapporti, indent=2, ensure_ascii=False))
        return
    if not rapporti:
        typer.echo("nessun client si e' ancora collegato: niente da misurare")
        return

    stati = {
        "no-usage": "mai usato",
        "ignores-scope": "scope ignorato",
        "search-only": "solo ricerca",
        "graph-only": "solo grafo, mai cerca",
        "no-recording": "cerca e naviga, non registra",
        "follows-profile": "profilo in vigore",
    }
    righe = [
        (
            (r["recognised_as"] or r["name"])[:22],
            str(r["calls"]),
            str(r["searches"]),
            "—" if r["scope_rate"] is None else f"{r['scope_rate']*100:.0f}%",
            str(r["graph_calls"]),
            str(r["task_steps"]),
            stati[r["verdict"]],
        )
        for r in rapporti
    ]
    intestazione = ("CLIENT", "CHIAM.", "RICER.", "SCOPE", "GRAFO", "REGISTR.", "STATO")
    larghezze = [max(len(r[i]) for r in [intestazione, *righe]) for i in range(7)]

    # I numeri a destra, il testo a sinistra: una colonna di cifre allineate a
    # sinistra si legge male, e questa tabella serve a confrontare righe.
    numeriche = {1, 2, 3, 4, 5}

    def _riga(valori, intestazione=False):
        celle = [
            v.rjust(w) if (i in numeriche and not intestazione) else v.ljust(w)
            for i, (v, w) in enumerate(zip(valori, larghezze))
        ]
        return "  ".join(celle).rstrip()

    typer.echo("  " + _riga(intestazione, intestazione=True))
    typer.echo("  " + "  ".join("-" * w for w in larghezze))
    for riga in righe:
        typer.echo("  " + _riga(riga))
    typer.echo("")
    typer.echo("  mai usato       collegato ma nessuna chiamata: il profilo probabilmente non e' arrivato")
    typer.echo("  scope ignorato  cerca senza scope in oltre un caso su tre")
    typer.echo("  solo ricerca    cerca bene, non usa il grafo ne' registra: meta' profilo in vigore")
    typer.echo("  solo grafo      usa il grafo ma non cerca mai: la prima regola non e' arrivata")
    typer.echo("  non registra    l'ultima regola, quella che nessun client esegue")
    typer.echo("")
    typer.echo("  uno scope basso puo' essere legittimo: le domande trasversali non lo passano")


@app.command("upgrade")
def upgrade(
    home: Path = typer.Option(Path.home(), "--home", help="User home directory for default paths."),
    db: Path | None = typer.Option(None, "--db", help="Path to the SQLite database."),
    project_root: Path = typer.Option(Path("."), "--project-root", help="Project root."),
    skip_graphs: bool = typer.Option(False, "--skip-graphs", help="Non ricostruire i grafi del codice."),
    skip_profile: bool = typer.Option(False, "--skip-profile", help="Non scrivere il profilo nei client."),
    json_output: bool = typer.Option(False, "--json", help="Stampa il rapporto come JSON."),
) -> None:
    """Porta un'installazione esistente alla versione corrente, in un comando.

    Dopo un aggiornamento del pacchetto restavano quattro passi da fare a mano —
    migrare lo schema, ricostruire i grafi, scrivere il profilo nei client,
    aggiornare l'indice — e quattro passi scritti in un manuale sono un compito
    affidato alla memoria di una persona. Questo comando li fa e dice cosa ha
    fatto.

    L'ordine non e' arbitrario: prima la migrazione con il suo backup, perche' se
    qualcosa va storto lì il resto non deve nemmeno cominciare.
    """
    from truenex_memory.core.config import resolve_project_config
    from truenex_memory.core.migration import migrate_apply, migration_status

    config = resolve_project_config(project_root)
    db_path = db if db is not None else config.db_path
    # I backup stanno accanto al database che si sta migrando, non nella cartella
    # del progetto corrente: con `--db` che punta all'archivio globale, la copia
    # di sicurezza finiva dentro un progetto qualsiasi — cioe' lontano da cio'
    # che protegge, dove nessuno la cercherebbe.
    backups_dir = db_path.parent / "backups"
    rapporto: dict[str, Any] = {"database": str(db_path)}

    # 1. Schema, col backup che l'apertura normale non fa.
    prima = migration_status(db_path)
    if prima["pending"]:
        esito = migrate_apply(db_path, backups_dir)
        rapporto["schema"] = {
            "da": esito["previous_version"],
            "a": esito["current_version"],
            "backup": esito["backup_path"],
        }
    else:
        rapporto["schema"] = {"da": prima["current_version"], "a": prima["current_version"], "backup": None}

    # 2. I grafi del codice: la cache ha cambiato formato, e una cache di una
    #    versione precedente risponderebbe «nessun chiamante» dove ce ne sono.
    rapporto["grafi"] = []
    if not skip_graphs:
        from truenex_memory.graph import GraphifyUnavailable, build_file_graph, save_file_graph
        from truenex_memory.adapters.profile import known_project_roots

        cache_dir = _graph_cache_dir(db, home)
        radici = known_project_roots(home)
        for radice in radici:
            try:
                grafo = build_file_graph(radice)
                save_file_graph(grafo, cache_dir)
                voce = {
                    "progetto": radice.name,
                    "file": grafo.stats.get("files", 0),
                    "esito": "ricostruito",
                }
                if not voce["file"]:
                    # Zero file non e' assenza di informazione: e' un grafo
                    # vuoto. Senza la ragione — nessun sorgente riconosciuto,
                    # oppure un linguaggio per cui non abbiamo la grammatica —
                    # e' indistinguibile da un successo, ed e' esattamente
                    # cosi' che si e' presentato sulla macchina vera: sei
                    # progetti .NET marcati «ricostruito» e vuoti dentro.
                    scartati = grafo.stats.get("skipped_by_suffix") or {}
                    cima = ", ".join(f"{n} {e}" for e, n in list(scartati.items())[:3])
                    voce["perche"] = (
                        f"nessun sorgente riconosciuto (scartati {cima})"
                        if cima
                        else "nessun file nella cartella"
                    )
                rapporto["grafi"].append(voce)
            except GraphifyUnavailable:
                rapporto["grafi"].append({"progetto": radice.name, "esito": "backend di estrazione assente"})
            except Exception as errore:  # pragma: no cover - un progetto rotto non ferma gli altri
                rapporto["grafi"].append({"progetto": radice.name, "esito": f"errore: {errore}"})

    # 3. Il profilo nei file dei client installati.
    rapporto["profilo"] = []
    if not skip_profile:
        from truenex_memory.adapters.profile import apply_all

        for voce in apply_all(home):
            if voce.installed:
                rapporto["profilo"].append({"client": voce.client, "esito": voce.action})

    if json_output:
        typer.echo(json.dumps(rapporto, indent=2, ensure_ascii=False, default=str))
        return

    s = rapporto["schema"]
    typer.echo(f"schema:  {s['da']} -> {s['a']}" + (f"   backup: {s['backup']}" if s["backup"] else ""))
    if rapporto["grafi"]:
        typer.echo("")
        typer.echo("grafi del codice:")
        for g in rapporto["grafi"]:
            if "file" not in g:
                dettaglio = ""
            elif g["file"]:
                dettaglio = f"{g['file']} file"
            else:
                dettaglio = f"0 file — {g.get('perche', 'motivo non registrato')}"
            typer.echo(f"  {g['progetto']:32s} {g['esito']:24s} {dettaglio}")
    elif not skip_graphs:
        # Su un'installazione nuova la cache dei grafi e' vuota, quindi non c'e'
        # niente da ricostruire — e tacere lascerebbe l'utente senza grafo senza
        # sapere perche'. Il primo grafo lo costruisce solo un comando esplicito:
        # la ricostruzione automatica aggiorna cio' che esiste, non lo crea.
        from truenex_memory.graph import graphify_available

        typer.echo("")
        if not graphify_available():
            # Il caso trovato aggiornando una macchina vera: il grafo e' la
            # capacita' principale di questa versione e dipende da un pacchetto
            # opzionale, ma niente lo diceva prima di provare a costruirlo. Un
            # requisito che si scopre da un errore e' un requisito nascosto.
            typer.echo("grafi del codice: manca il pacchetto per estrarli")
            typer.echo('  installalo con: pip install --upgrade "truenex-memory[graph]"')
            typer.echo("  (con pipx: pipx install --force \"truenex-memory[graph]\")")
        else:
            typer.echo("grafi del codice: nessuno da ricostruire (non ne esiste ancora)")
            typer.echo("  il primo si costruisce con: truenex-mem graph build <cartella del progetto>")
    if rapporto["profilo"]:
        typer.echo("")
        typer.echo("profilo nei client:")
        for p in rapporto["profilo"]:
            typer.echo(f"  {p['client']:16s} {p['esito']}")
    typer.echo("")
    typer.echo("fatto. `truenex-mem status` per il quadro completo.")

"""CLI entry point for Truenex Memory."""

from dataclasses import asdict
from pathlib import Path
import json
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
from truenex_memory.release.manifest import DEFAULT_MANIFEST_URL
from truenex_memory.release.update_check import check_for_updates
from truenex_memory.release.version import get_version_info
from truenex_memory.core.config import resolve_project_config
from truenex_memory.core.migration import list_backups
from truenex_memory.core.migration import migrate_apply as apply_migrations
from truenex_memory.core.migration import migration_status
from truenex_memory.core.migration import restore_backup
from truenex_memory.discovery.agent_discovery import (
    DEFAULT_DISPLAY_LIMIT,
    discover_from_agents,
    format_report,
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
from truenex_memory.ingestion.global_status import (
    build_global_status,
    format_status_report,
)
from truenex_memory.ingestion.global_source_health import (
    build_source_health,
    format_source_health_report,
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
app.add_typer(adapter_app, name="adapter")
app.add_typer(update_app, name="update")
app.add_typer(migrate_app, name="migrate")
app.add_typer(status_app, name="status")
app.add_typer(ingest_app, name="ingest")
app.add_typer(trace_app, name="trace")
global_app.add_typer(sources_app, name="sources")
global_app.add_typer(auto_app, name="auto")
app.add_typer(global_app, name="global")
app.add_typer(task_app, name="task")


@app.callback()
def callback() -> None:
    """Truenex Memory - local-first memory for coding agents."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


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

    service = MemoryService(".")
    service.init_project()
    typer.echo(f"Initialized {service.config.data_dir}")


@app.command()
def add(
    content: str = typer.Argument(..., help="Memory content to store."),
    memory_type: str = typer.Option("note", "--type", help="Memory type, e.g. note or decision."),
) -> None:
    """Add a manual memory node."""

    memory_id = MemoryService(".").add(content, memory_type=memory_type)
    typer.echo(memory_id)


@app.command("list")
def list_command(
    status: str | None = typer.Option(None, "--status", help="Filter by lifecycle status."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """List manual memory nodes."""

    if status is not None:
        _validate_status(status)
    memories = MemoryService(".").list_memory_nodes(status=status)
    if json_output:
        typer.echo(json.dumps([asdict(memory) for memory in memories], indent=2, sort_keys=True))
        return
    for memory in memories:
        typer.echo(f"{memory.id} {memory.status} {memory.type} {memory.title}")


@app.command()
def index(path: Path = typer.Argument(Path("."), help="File or directory to index.")) -> None:
    """Index local files into the project memory store."""

    if not path.exists():
        raise typer.BadParameter(f"path does not exist: {path}")
    count = MemoryService(".").index(path)
    typer.echo(f"Indexed {count} file(s)")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    top_k: int = typer.Option(5, "--top-k", min=1, max=50, help="Maximum results."),
    json_output: bool = typer.Option(False, "--json", help="Print full JSON payload."),
    include_inactive: bool = typer.Option(
        False, "--include-inactive", help="Include inactive (e.g. obsolete) memories in results."
    ),
) -> None:
    """Search local memory."""

    service = MemoryService(".")
    results = service.search(query, top_k=top_k, include_inactive=include_inactive)
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

    service = MemoryService(".")
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

    service = MemoryService(".")
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


@status_app.command("set")
def status_set(
    memory_id: str = typer.Argument(..., help="Memory node id."),
    status: str = typer.Argument(..., help="New lifecycle status."),
) -> None:
    """Set a memory node lifecycle status."""

    _validate_status(status)
    try:
        MemoryService(".").set_memory_status(memory_id, status)
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

    Scans Codex (.codex/sessions, .codex/memories) and Claude
    (.claude/projects, .claude/commands) directories to find:
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
    """Search the global store without mutating retrieval logs or DB state."""
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


if __name__ == "__main__":
    app()

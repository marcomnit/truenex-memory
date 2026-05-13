"""CLI commands for the adaptive task pipeline (truenex-mem task ...)."""
from __future__ import annotations
from pathlib import Path
import json
import typer
from truenex_memory.store.task_store import TaskStore, TASK_TYPES

task_app = typer.Typer(help="Manage adaptive task pipeline records.")
_DEFAULT_DB = Path.home() / ".truenex-memory" / "truenex_memory.db"


def _store(db: Path | None = None) -> TaskStore:
    return TaskStore(db or _DEFAULT_DB)


@task_app.command("open")
def task_open(
    title: str = typer.Argument(..., help="Short task description."),
    task_type: str = typer.Option("feature", "--type", "-t", help="Task type: bugfix|feature|refactor|review|query"),
    project: str = typer.Option(None, "--project", "-p", help="Project name."),
    session: str = typer.Option(None, "--session", help="Agent session ID."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Open a new task record."""
    if task_type not in TASK_TYPES:
        typer.echo(f"Error: --type must be one of {sorted(TASK_TYPES)}", err=True)
        raise typer.Exit(code=1)
    task_id = _store().task_open(title, task_type, project=project, agent_session_id=session)
    if json_out:
        typer.echo(json.dumps({"task_id": task_id, "status": "open"}, indent=2))
    else:
        typer.echo(f"Task opened: {task_id}")


@task_app.command("close")
def task_close(
    task_id: str = typer.Argument(..., help="Task ID to close."),
    outcome: int = typer.Option(None, "--outcome", "-o", help="Human outcome: 1=positive, 0=partial, -1=negative."),
    comment: str = typer.Option(None, "--comment", "-c", help="Optional human comment."),
    unrated: bool = typer.Option(False, "--unrated", help="Mark as unrated (no judgment)."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Close a task with optional human judgment."""
    store = _store()
    if unrated:
        human_outcome = None
        human_comment = None
    elif outcome is not None:
        if outcome not in (1, 0, -1):
            typer.echo("Error: --outcome must be 1, 0, or -1", err=True)
            raise typer.Exit(code=1)
        human_outcome = outcome
        human_comment = comment
    else:
        try:
            task = store.task_get(task_id)
        except LookupError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"\nTask: {task.title}")
        typer.echo(f"Steps: {len(store.step_list(task_id))}")
        raw = typer.prompt("Human outcome (1=positive, 0=partial, -1=negative, skip=unrated)", default="skip")
        if raw.strip().lower() in ("skip", ""):
            human_outcome = None
            human_comment = None
        else:
            try:
                human_outcome = int(raw)
                if human_outcome not in (1, 0, -1):
                    raise ValueError
            except ValueError:
                typer.echo("Invalid input — marking as unrated.", err=True)
                human_outcome = None
            human_comment = typer.prompt("Comment (optional, press Enter to skip)", default="") or None
    try:
        record = store.task_close(task_id, human_outcome=human_outcome, human_comment=human_comment)
    except LookupError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if json_out:
        typer.echo(json.dumps({"task_id": record.task_id, "status": record.status, "human_outcome": record.human_outcome}, indent=2))
    else:
        typer.echo(f"Task closed: {record.task_id} — status={record.status}, outcome={record.human_outcome}")


@task_app.command("list")
def task_list(
    project: str = typer.Option(None, "--project", "-p"),
    status: str = typer.Option(None, "--status", "-s", help="Filter: open|closed|unrated"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List recent tasks."""
    try:
        records = _store().task_list(project=project, status=status, limit=limit)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if json_out:
        typer.echo(json.dumps([r.__dict__ for r in records], indent=2))
        return
    if not records:
        typer.echo("No tasks found.")
        return
    for r in records:
        outcome = r.human_outcome if r.human_outcome is not None else "?"
        typer.echo(f"{r.task_id}  [{r.status}] [{r.type}] outcome={outcome}  {r.title[:60]}")


@task_app.command("show")
def task_show(
    task_id: str = typer.Argument(..., help="Task ID."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show task details with steps."""
    store = _store()
    try:
        task = store.task_get(task_id)
        steps = store.step_list(task_id)
    except LookupError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if json_out:
        typer.echo(json.dumps({"task": task.__dict__, "steps": [s.__dict__ for s in steps]}, indent=2))
        return
    typer.echo(f"Task:    {task.task_id}")
    typer.echo(f"Title:   {task.title}")
    typer.echo(f"Type:    {task.type}  Status: {task.status}")
    typer.echo(f"Project: {task.project or 'N/A'}")
    typer.echo(f"Outcome: {task.human_outcome}  Comment: {task.human_comment or 'N/A'}")
    typer.echo(f"Tokens:  {task.total_tokens}  Duration: {task.total_duration_s}s")
    typer.echo(f"Created: {task.created_at}  Closed: {task.closed_at or 'N/A'}")
    typer.echo(f"\nSteps ({len(steps)}):")
    for s in steps:
        typer.echo(f"  [{s.step_index}] judgment={s.brain_judgment} tokens={s.tokens_used} model={s.model_used}")


@task_app.command("calibration")
def task_calibration(
    project: str = typer.Option(None, "--project", "-p"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show calibration stats."""
    data = _store().calibration(project=project)
    if json_out:
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo("=== Verifier Acceptance ===")
    for row in data["verifier_acceptance"]:
        rate = row["acceptance_rate"]
        rate_str = f"{rate:.1%}" if rate is not None else "N/A"
        typer.echo(f"  {row['suggestion_type']}: {rate_str} ({row['accepted']}/{row['total']})")
    typer.echo("\n=== Brain vs Human Alignment ===")
    for row in data["brain_human_alignment"]:
        typer.echo(f"  brain={row['brain_judgment']} human={row['human_outcome']}: {row['count']}")

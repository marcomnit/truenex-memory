"""CLI commands for recursive multi-agent orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from truenex_memory.core.memory_service import MemoryService
from truenex_memory.orchestration.recursive_loop import (
    RecursiveLoop,
    RecursiveLoopConfig,
    RecursiveLoopReport,
)
from truenex_memory.store.task_store import TaskStore

orchestrate_app = typer.Typer(help="Recursive multi-agent loop orchestration.")
_DEFAULT_DB = Path.home() / ".truenex-memory" / "truenex_memory.db"


def _memory_service(project_root: Path | None = None) -> MemoryService:
    return MemoryService(project_root or Path("."))


def _task_store(memory: MemoryService) -> TaskStore:
    return TaskStore(memory.config.db_path)


def _format_report(report: RecursiveLoopReport) -> str:
    lines = [
        f"Loop:      {report.loop_name}",
        f"Task:      {report.task_id or '—'}",
        f"Iterations:{report.iterations_run}/{report.max_depth}",
        f"Converged: {'yes' if report.converged else 'no'}",
    ]
    if report.convergence_iteration:
        lines.append(f"At iter:   {report.convergence_iteration}")
    lines.append(f"Rounds:    {len(report.round_ids)}")
    if report.error:
        lines.append(f"Error:     {report.error}")
    return "\n".join(lines)


@orchestrate_app.command("run")
def orchestrate_run(
    config_path: Path = typer.Argument(..., help="Path to loop config JSON file."),
    project_root: Path | None = typer.Option(None, "--project", "-p", help="Project root directory."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Execute a recursive loop from a JSON config file."""

    if not config_path.exists():
        typer.echo(f"Error: config file not found: {config_path}", err=True)
        raise typer.Exit(code=1)

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        config = RecursiveLoopConfig.from_dict(raw)
        config.validate()
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        typer.echo(f"Error: invalid config file: {exc}", err=True)
        raise typer.Exit(code=1)

    memory = _memory_service(project_root)
    store = _task_store(memory)

    loop = RecursiveLoop(config, memory, task_store=store)
    report = loop.run()

    if json_out:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        typer.echo(_format_report(report))

    if report.error:
        raise typer.Exit(code=2)
    if not report.converged:
        raise typer.Exit(code=3)


@orchestrate_app.command("converge-check")
def orchestrate_converge_check(
    left: str = typer.Argument(..., help="Memory ID of first round/summary."),
    right: str = typer.Argument(..., help="Memory ID of second round/summary."),
    project_root: Path | None = typer.Option(None, "--project", "-p"),
) -> None:
    """Check whether two persisted rounds are byte-identical."""

    memory = _memory_service(project_root)
    node_left = memory.get_memory_node(left)
    node_right = memory.get_memory_node(right)

    if node_left is None:
        typer.echo(f"Error: left ID not found: {left}", err=True)
        raise typer.Exit(code=1)
    if node_right is None:
        typer.echo(f"Error: right ID not found: {right}", err=True)
        raise typer.Exit(code=1)

    equal = node_left.content == node_right.content
    typer.echo(f"equal={equal}")
    raise typer.Exit(code=0 if equal else 1)

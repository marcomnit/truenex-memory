"""Command-line entry point for agent adapter file generation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from truenex_memory.adapters import write_agent_docs

app = typer.Typer(help="Generate local agent adapter instruction files.")


@app.command("generate")
def generate(
    directory: Annotated[Path, typer.Argument(help="Directory where files are written.")],
    project_name: Annotated[str, typer.Option("--project-name")] = "Truenex Memory",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Generate AGENTS.md and CLAUDE.md."""

    written = write_agent_docs(directory, project_name=project_name, overwrite=overwrite)
    for name, path in written.items():
        typer.echo(f"{name}: {path}")


if __name__ == "__main__":
    app()

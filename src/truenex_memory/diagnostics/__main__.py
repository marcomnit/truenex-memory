"""Command-line entry point for local diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from truenex_memory.diagnostics import run_diagnostics

app = typer.Typer(help="Run local Truenex Memory diagnostics.")


@app.command()
def run(
    base_path: Annotated[
        Path | None,
        typer.Option("--base-path", help="Local path to validate for filesystem access."),
    ] = None,
) -> None:
    """Print diagnostics as JSON."""

    typer.echo(json.dumps(run_diagnostics(base_path), indent=2, sort_keys=True))


if __name__ == "__main__":
    app()

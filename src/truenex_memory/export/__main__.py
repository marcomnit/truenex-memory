"""Command-line entry point for JSON export/import helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from truenex_memory.export import export_json, import_json

app = typer.Typer(help="Local JSON export/import utilities.")


@app.command("export")
def export_command(
    source: Annotated[Path, typer.Argument(help="JSON file containing a list of records.")],
    destination: Annotated[Path, typer.Argument(help="Export file to write.")],
) -> None:
    """Wrap a local records JSON file in the Truenex export envelope."""

    records = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise typer.BadParameter("source must contain a JSON list of record objects")
    payload = export_json(records, destination)
    typer.echo(json.dumps({"destination": str(destination), "records": len(payload["records"])}, sort_keys=True))


@app.command("import")
def import_command(
    source: Annotated[Path, typer.Argument(help="Export file to read.")],
) -> None:
    """Print records from a Truenex export file as JSON."""

    typer.echo(json.dumps(import_json(source)["records"], indent=2, sort_keys=True))


if __name__ == "__main__":
    app()

"""Command-line entry point for local MCP-style tools."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from truenex_memory.mcp import call_tool, list_tools

app = typer.Typer(help="Invoke local MCP-style tools without starting a server.")


@app.command("list")
def list_command() -> None:
    """List available tools."""

    typer.echo(json.dumps(list_tools(), indent=2, sort_keys=True))


@app.command("call")
def call_command(
    name: Annotated[str, typer.Argument(help="Tool name.")],
    arguments: Annotated[str, typer.Option("--arguments", help="JSON object with tool arguments.")] = "{}",
) -> None:
    """Call a tool with JSON arguments."""

    parsed = json.loads(arguments)
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--arguments must be a JSON object")
    typer.echo(json.dumps(call_tool(name, parsed), indent=2, sort_keys=True))


if __name__ == "__main__":
    app()

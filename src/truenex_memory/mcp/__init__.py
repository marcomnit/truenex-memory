"""Local MCP-style tool registry.

This module intentionally does not start an MCP server. It exposes local tool
operations as plain Python callables so tests and local automation can invoke
them without network or cloud dependencies. The stdio MCP server exposes a
smaller explicit agent-facing subset in `truenex_memory.mcp.server`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from truenex_memory.adapters import render_agents_md, render_claude_md, write_agent_docs
from truenex_memory.diagnostics import run_diagnostics
from truenex_memory.export import export_json, import_json
from truenex_memory.mcp.tools import global_project_context, global_status, memory_add, memory_search

Tool = Callable[..., Any]


def _tool_export_json(records: list[dict[str, Any]], destination: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = export_json(records, destination, metadata=metadata)
    return {"destination": str(Path(destination)), "records": len(payload["records"]), "payload": payload}


def _tool_import_json(source: str) -> dict[str, Any]:
    payload = import_json(source)
    return {"source": str(Path(source)), "records": len(payload["records"]), "payload": payload}


def _tool_write_agent_docs(directory: str, project_name: str = "Truenex Memory", overwrite: bool = False) -> dict[str, str]:
    written = write_agent_docs(directory, project_name=project_name, overwrite=overwrite)
    return {name: str(path) for name, path in written.items()}


TOOLS: dict[str, Tool] = {
    "global_project_context": global_project_context,
    "global_status": global_status,
    "memory_add": memory_add,
    "memory_search": memory_search,
    "diagnostics.run": run_diagnostics,
    "export.json": _tool_export_json,
    "import.json": _tool_import_json,
    "adapters.render_agents_md": render_agents_md,
    "adapters.render_claude_md": render_claude_md,
    "adapters.write_agent_docs": _tool_write_agent_docs,
}


def list_tools() -> list[dict[str, str]]:
    """List local MCP-style tools."""

    return [{"name": name, "description": (tool.__doc__ or "").strip()} for name, tool in sorted(TOOLS.items())]


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Invoke a local MCP-style tool by name."""

    try:
        tool = TOOLS[name]
    except KeyError as exc:
        raise KeyError(f"unknown tool: {name}") from exc
    return tool(**(arguments or {}))


__all__ = ["TOOLS", "call_tool", "list_tools"]

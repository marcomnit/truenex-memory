"""MCP stdio server for Truenex Memory.

The stdio transport uses newline-delimited JSON-RPC messages. This module keeps
the implementation dependency-free so local tests can exercise the MCP contract
without installing the optional Python MCP SDK.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from truenex_memory import __version__
from truenex_memory.mcp.tools import (
    global_project_context, global_status, memory_add, memory_search,
    task_open, task_step_add, task_close,
)


SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

JSONRPC_VERSION = "2.0"


def run_stdio_server(project_root: Path | str = ".") -> None:
    """Run the MCP stdio server."""

    root = Path(project_root).resolve()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = handle_jsonrpc_line(line, project_root=root)
        if response is None:
            continue
        print(json.dumps(response, separators=(",", ":"), sort_keys=True), flush=True)


def handle_jsonrpc_line(line: str, *, project_root: Path | str = ".") -> dict[str, Any] | list[dict[str, Any]] | None:
    """Handle one newline-delimited JSON-RPC message or batch."""

    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error_response(None, -32700, "Parse error", str(exc))

    if isinstance(message, list):
        responses = [
            response
            for item in message
            if (response := handle_jsonrpc_message(item, project_root=project_root)) is not None
        ]
        return responses or None
    return handle_jsonrpc_message(message, project_root=project_root)


def handle_jsonrpc_message(message: object, *, project_root: Path | str = ".") -> dict[str, Any] | None:
    """Handle one JSON-RPC request or notification."""

    if not isinstance(message, dict):
        return _error_response(None, -32600, "Invalid Request", "message must be an object")
    request_id = message.get("id")
    method = message.get("method")
    if message.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str):
        return _error_response(request_id, -32600, "Invalid Request", "expected JSON-RPC 2.0 request")

    is_notification = "id" not in message
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None if is_notification else _error_response(request_id, -32602, "Invalid params")

    try:
        result = _dispatch(method, params, project_root=Path(project_root))
    except KeyError as exc:
        return None if is_notification else _error_response(request_id, -32601, "Method not found", str(exc))
    except ValueError as exc:
        return None if is_notification else _error_response(request_id, -32602, "Invalid params", str(exc))
    except Exception as exc:  # pragma: no cover - defensive protocol boundary
        return None if is_notification else _error_response(request_id, -32603, "Internal error", str(exc))

    if is_notification:
        return None
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _dispatch(method: str, params: dict[str, Any], *, project_root: Path) -> dict[str, Any]:
    if method == "initialize":
        return _initialize(params)
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": _tool_definitions()}
    if method == "tools/call":
        return _call_tool(params, project_root=project_root)
    if method.startswith("notifications/"):
        return {}
    raise KeyError(method)


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    protocol_version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "truenex-memory", "version": __version__},
    }


def _tool_definitions() -> list[dict[str, Any]]:
    """Return the explicit stdio MCP tool surface.

    This is intentionally smaller than the local Python registry in
    `truenex_memory.mcp`: agent-facing stdio tools are limited to memory and
    read-only global bootstrap operations.
    """
    return [
        {
            "name": "memory_search",
            "description": "Search local Truenex Memory for project context and source-backed decisions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language query for project memory.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 5,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "memory_add",
            "description": "Add a local memory note or decision for the current project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Memory content to store locally.",
                    },
                    "memory_type": {
                        "type": "string",
                        "description": "Memory type, such as note or decision.",
                        "default": "note",
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "global_status",
            "description": "Read-only report of the Truenex Memory global store status (catalog, database, ledger, indexed, problems).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "home": {
                        "type": "string",
                        "description": "Override the global home directory (default: user home).",
                    },
                    "catalog": {
                        "type": "string",
                        "description": "Override path to sources.json catalog.",
                    },
                    "db": {
                        "type": "string",
                        "description": "Override path to SQLite global database.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "global_project_context",
            "description": "Read-only project context report from the Truenex Memory global store (catalog roots, ledger, indexed documents/chunks). Server aliases are hints only; no SSH/network/DB execution.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name, path, or alias to resolve.",
                    },
                    "home": {
                        "type": "string",
                        "description": "Override the global home directory (default: user home).",
                    },
                    "catalog": {
                        "type": "string",
                        "description": "Override path to sources.json catalog.",
                    },
                    "db": {
                        "type": "string",
                        "description": "Override path to SQLite global database.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum indexed documents/chunks to return.",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                },
                "required": ["project"],
                "additionalProperties": False,
            },
        },
        {
            "name": "task_open",
            "description": "Open a new adaptive task record. Call at the start of every agent session to enable outcome tracking.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short description of the task."},
                    "task_type": {"type": "string", "enum": ["bugfix", "feature", "refactor", "review", "query"], "default": "feature"},
                    "project": {"type": "string", "description": "Project name (e.g. AI_Agent)."},
                    "agent_session_id": {"type": "string", "description": "Current agent session ID."},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        {
            "name": "task_step_add",
            "description": "Record a step taken within the current task (prompt, output, brain judgment, token usage).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID returned by task_open."},
                    "prompt_used": {"type": "string"},
                    "output": {"type": "string"},
                    "brain_judgment": {"type": "string", "enum": ["ok", "needs_revision", "rejected"]},
                    "tokens_used": {"type": "integer"},
                    "duration_s": {"type": "number"},
                    "model_used": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "task_close",
            "description": "Close the current task. Ask the human for a quality judgment (1=positive, 0=partial, -1=negative) before calling. Omit human_outcome if the human did not respond.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "human_outcome": {"type": "integer", "enum": [1, 0, -1]},
                    "human_comment": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
    ]


def _call_tool(params: dict[str, Any], *, project_root: Path) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not name:
        raise ValueError("tool name is required")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")

    try:
        if name == "memory_search":
            payload = _call_memory_search(arguments, project_root=project_root)
        elif name == "memory_add":
            payload = _call_memory_add(arguments, project_root=project_root)
        elif name == "global_status":
            payload = _call_global_status(arguments)
        elif name == "global_project_context":
            payload = _call_global_project_context(arguments)
        elif name == "task_open":
            payload = _call_task_open(arguments)
        elif name == "task_step_add":
            payload = _call_task_step_add(arguments)
        elif name == "task_close":
            payload = _call_task_close(arguments)
        else:
            raise ValueError(f"unknown tool: {name}")
        return _tool_result(payload, is_error=False)
    except Exception as exc:
        return _tool_result({"error": str(exc)}, is_error=True)


def _call_memory_search(arguments: dict[str, Any], *, project_root: Path) -> dict[str, object]:
    query = arguments.get("query")
    top_k = arguments.get("top_k", 5)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(top_k, int):
        raise ValueError("top_k must be an integer")
    if top_k < 1 or top_k > 50:
        raise ValueError("top_k must be between 1 and 50")
    return memory_search(query, top_k=top_k, project_root=project_root)


def _call_memory_add(arguments: dict[str, Any], *, project_root: Path) -> dict[str, object]:
    content = arguments.get("content")
    memory_type = arguments.get("memory_type", "note")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    if not isinstance(memory_type, str) or not memory_type.strip():
        raise ValueError("memory_type must be a non-empty string")
    return memory_add(content, memory_type=memory_type, project_root=project_root)


def _call_global_status(arguments: dict[str, Any]) -> dict[str, object]:
    home = arguments.get("home")
    catalog = arguments.get("catalog")
    db = arguments.get("db")

    for name, val in (("home", home), ("catalog", catalog), ("db", db)):
        if val is not None and not isinstance(val, str):
            raise ValueError(f"{name} must be a string")

    return global_status(home=home, catalog=catalog, db=db)


def _call_global_project_context(arguments: dict[str, Any]) -> dict[str, object]:
    project = arguments.get("project")
    home = arguments.get("home")
    catalog = arguments.get("catalog")
    db = arguments.get("db")
    limit = arguments.get("limit", 20)

    if not isinstance(project, str) or not project.strip():
        raise ValueError("project must be a non-empty string")
    for name, val in (("home", home), ("catalog", catalog), ("db", db)):
        if val is not None and not isinstance(val, str):
            raise ValueError(f"{name} must be a string")
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        raise ValueError("limit must be an integer between 1 and 100")

    return global_project_context(
        project=project,
        home=home,
        catalog=catalog,
        db=db,
        limit=limit,
    )


def _call_task_open(arguments: dict[str, Any]) -> dict[str, object]:
    title = arguments.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    return task_open(
        title,
        arguments.get("task_type", "feature"),
        project=arguments.get("project"),
        agent_session_id=arguments.get("agent_session_id"),
    )


def _call_task_step_add(arguments: dict[str, Any]) -> dict[str, object]:
    task_id = arguments.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    return task_step_add(
        task_id,
        prompt_used=arguments.get("prompt_used"),
        output=arguments.get("output"),
        brain_judgment=arguments.get("brain_judgment"),
        tokens_used=arguments.get("tokens_used"),
        duration_s=arguments.get("duration_s"),
        model_used=arguments.get("model_used"),
    )


def _call_task_close(arguments: dict[str, Any]) -> dict[str, object]:
    task_id = arguments.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    human_outcome = arguments.get("human_outcome")
    if human_outcome is not None and human_outcome not in (1, 0, -1):
        raise ValueError("human_outcome must be 1, 0, or -1")
    return task_close(task_id, human_outcome=human_outcome, human_comment=arguments.get("human_comment"))


def _tool_result(payload: dict[str, object], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "isError": is_error,
    }


def _error_response(
    request_id: object,
    code: int,
    message: str,
    data: object | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}

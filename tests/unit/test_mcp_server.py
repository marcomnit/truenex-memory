"""Tests for MCP JSON-RPC stdio protocol handling."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from truenex_memory.mcp.server import handle_jsonrpc_line, handle_jsonrpc_message


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / f"{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def test_initialize_declares_tools_capability() -> None:
    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}},
        }
    )

    assert response is not None
    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert response["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert response["result"]["serverInfo"]["name"] == "truenex-memory"


def test_tools_list_returns_memory_tool_schemas() -> None:
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})

    assert response is not None
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    assert {"memory_search", "memory_add"} <= set(tools)
    assert tools["memory_search"]["inputSchema"]["required"] == ["query"]
    assert tools["memory_add"]["inputSchema"]["required"] == ["content"]


def test_tools_list_includes_global_bootstrap_schemas() -> None:
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "tools2", "method": "tools/list"})

    assert response is not None
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    assert "global_status" in tools
    assert "global_project_context" in tools
    assert tools["global_status"]["inputSchema"]["type"] == "object"
    assert tools["global_project_context"]["inputSchema"]["required"] == ["project"]
    # limit must have bounds
    limit_schema = tools["global_project_context"]["inputSchema"]["properties"]["limit"]
    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == 100


def test_stdio_tool_surface_is_safe_subset_of_local_registry() -> None:
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "tools-subset", "method": "tools/list"})

    assert response is not None
    tools = {tool["name"] for tool in response["result"]["tools"]}
    assert tools == {
        "memory_search",
        "memory_add",
        "global_status",
        "global_project_context",
        "task_open",
        "task_step_add",
        "task_close",
    }


def test_tools_call_add_and_search() -> None:
    project_root = _workdir("mcp_protocol_tools")
    add_response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "memory_add",
                "arguments": {"content": "MCP uses local SQLite memory.", "memory_type": "decision"},
            },
        },
        project_root=project_root,
    )
    search_response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "memory_search", "arguments": {"query": "local SQLite", "top_k": 1}},
        },
        project_root=project_root,
    )

    assert add_response is not None
    assert add_response["result"]["isError"] is False
    assert search_response is not None
    result_text = search_response["result"]["content"][0]["text"]
    payload = json.loads(result_text)
    assert payload["results"]
    assert payload["results"][0]["memory_type"] == "decision"


def test_tool_errors_are_reported_as_tool_results() -> None:
    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "memory_search", "arguments": {"query": "", "top_k": 1}},
        },
        project_root=_workdir("mcp_protocol_errors"),
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert "query" in response["result"]["content"][0]["text"]


def test_global_project_context_validates_required_project() -> None:
    response = handle_jsonrpc_message({
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "global_project_context",
            "arguments": {},
        },
    })

    assert response is not None
    assert response["result"]["isError"] is True
    assert "project" in response["result"]["content"][0]["text"]


def test_global_project_context_validates_limit() -> None:
    response = handle_jsonrpc_message({
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "global_project_context",
            "arguments": {"project": "test", "limit": 200},
        },
    })

    assert response is not None
    assert response["result"]["isError"] is True
    assert "limit" in response["result"]["content"][0]["text"]


def test_global_status_mcp_call_returns_json_content() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        response = handle_jsonrpc_message({
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {
                "name": "global_status",
                "arguments": {"home": tmp},
            },
        })

    assert response is not None
    assert response["result"]["isError"] is False
    payload = json.loads(response["result"]["content"][0]["text"])
    assert "catalog" in payload
    assert "database" in payload
    assert "warnings" in payload
    assert payload["catalog"]["exists"] is False


def test_global_project_context_mcp_call_with_custom_catalog() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        truenex_dir = home / ".truenex-memory"
        truenex_dir.mkdir(parents=True)

        catalog_path = truenex_dir / "sources.json"
        catalog_data = {
            "version": "1",
            "entries": [
                {
                    "id": "root-x",
                    "source_type": "project_root",
                    "path_or_alias": "/opt/proj",
                    "project_name": "OptProject",
                    "confirmation_status": "confirmed",
                },
            ],
        }
        catalog_path.write_text(json.dumps(catalog_data))

        response = handle_jsonrpc_message({
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "global_project_context",
                "arguments": {"project": "OptProject", "home": tmp},
            },
        })

    assert response is not None
    assert response["result"]["isError"] is False
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["resolved"] is True
    assert payload["resolution_method"] == "exact_name"
    assert len(payload["catalog"]["roots"]) == 1


def test_notifications_do_not_emit_responses() -> None:
    assert handle_jsonrpc_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_invalid_json_returns_parse_error() -> None:
    response = handle_jsonrpc_line("{bad json")

    assert response is not None
    assert response["error"]["code"] == -32700


def test_batch_filters_notifications() -> None:
    response = handle_jsonrpc_line(
        json.dumps(
            [
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 5, "method": "ping"},
            ]
        )
    )

    assert response == [{"jsonrpc": "2.0", "id": 5, "result": {}}]


def test_stdio_server_speaks_newline_delimited_jsonrpc() -> None:
    project_root = _workdir("mcp_protocol_stdio")
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(repo_root / "src")
    request = {
        "jsonrpc": "2.0",
        "id": "list",
        "method": "tools/list",
    }
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "truenex_memory.cli.main",
            "mcp",
            "--project-root",
            str(project_root),
        ],
        env=env,
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        check=True,
    )

    response = json.loads(process.stdout)
    assert response["id"] == "list"
    assert response["result"]["tools"][0]["name"] == "memory_search"


def test_stdio_server_calls_global_status_without_creating_home() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        env = os.environ.copy()
        repo_root = Path(__file__).resolve().parents[2]
        env["PYTHONPATH"] = str(repo_root / "src")
        request = {
            "jsonrpc": "2.0",
            "id": "global-status",
            "method": "tools/call",
            "params": {
                "name": "global_status",
                "arguments": {"home": str(home)},
            },
        }
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "truenex_memory.cli.main",
                "mcp",
                "--project-root",
                str(Path(tmp) / "project"),
            ],
            env=env,
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            check=True,
        )

        response = json.loads(process.stdout)
        payload = json.loads(response["result"]["content"][0]["text"])
        assert response["id"] == "global-status"
        assert response["result"]["isError"] is False
        assert payload["catalog"]["exists"] is False
        assert not home.exists()

"""End-to-end MCP stdio session tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


@pytest.mark.e2e
def test_mcp_stdio_session_adds_and_searches_memory() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    project_root = repo_root / "tests" / "e2e" / f"task_work_mcp_{uuid.uuid4().hex}"
    shutil.rmtree(project_root, ignore_errors=True)
    project_root.mkdir(parents=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    requests = [
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "e2e", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": "add",
            "method": "tools/call",
            "params": {
                "name": "memory_add",
                "arguments": {
                    "content": "E2E MCP stores memory in local SQLite.",
                    "memory_type": "decision",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": "search",
            "method": "tools/call",
            "params": {
                "name": "memory_search",
                "arguments": {"query": "local SQLite", "top_k": 1},
            },
        },
    ]

    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "truenex_memory.cli.main",
            "mcp",
            "--project-root",
            str(project_root),
        ],
        input="\n".join(json.dumps(request) for request in requests) + "\n",
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    responses = [json.loads(line) for line in process.stdout.splitlines()]
    by_id = {response["id"]: response for response in responses}
    assert by_id["init"]["result"]["serverInfo"]["name"] == "truenex-memory"
    tool_names = {tool["name"] for tool in by_id["tools"]["result"]["tools"]}
    assert {"memory_add", "memory_search"} <= tool_names
    assert by_id["add"]["result"]["isError"] is False
    assert by_id["search"]["result"]["isError"] is False
    search_payload = json.loads(by_id["search"]["result"]["content"][0]["text"])
    assert search_payload["results"]
    assert search_payload["results"][0]["memory_type"] == "decision"

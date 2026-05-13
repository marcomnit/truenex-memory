"""Tests for MCP-compatible tool functions."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from truenex_memory.mcp import call_tool, list_tools
from truenex_memory.mcp.tools import global_project_context, global_status, memory_add, memory_search


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task_work") / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def test_memory_add_and_search() -> None:
    project_root = _workdir("mcp")
    memory_add("Agents must cite local sources.", "decision", project_root=project_root)

    payload = memory_search("cite sources", project_root=project_root)

    assert payload["results"]
    assert payload["results"][0]["memory_type"] == "decision"
    assert str(payload["trace_id"]).startswith("ret_")


def test_tool_registry_includes_memory_contract() -> None:
    names = {tool["name"] for tool in list_tools()}

    assert {"memory_search", "memory_add"} <= names


def test_registry_includes_global_bootstrap_tools() -> None:
    names = {tool["name"] for tool in list_tools()}

    assert "global_status" in names
    assert "global_project_context" in names


def test_call_tool_dispatches_memory_tools() -> None:
    project_root = _workdir("mcp_registry")

    added = call_tool(
        "memory_add",
        {
            "content": "Local MCP tools use SQLite storage.",
            "memory_type": "decision",
            "project_root": project_root,
        },
    )
    found = call_tool("memory_search", {"query": "SQLite storage", "project_root": project_root})

    assert added["memory_type"] == "decision"
    assert found["results"]


def test_global_status_missing_paths_returns_warnings() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        result = global_status(home=str(home))
        catalog_path = home / ".truenex-memory" / "sources.json"
        db_path = home / ".truenex-memory" / "truenex_memory.db"

        assert not catalog_path.exists()
        assert not db_path.exists()

    assert isinstance(result, dict)
    assert "catalog" in result
    assert "database" in result
    assert "warnings" in result
    assert result["catalog"]["exists"] is False
    assert result["database"]["exists"] is False
    assert len(result["warnings"]) >= 2


def test_global_project_context_from_custom_catalog() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        truenex_dir = home / ".truenex-memory"
        truenex_dir.mkdir(parents=True)

        catalog_path = truenex_dir / "sources.json"
        catalog_data = {
            "version": "1",
            "entries": [
                {
                    "id": "root-1",
                    "source_type": "project_root",
                    "path_or_alias": "/home/user/myproject",
                    "project_name": "MyProject",
                    "confirmation_status": "confirmed",
                },
                {
                    "id": "doc-1",
                    "source_type": "document",
                    "path_or_alias": "/home/user/myproject/README.md",
                    "discovered_from": ["root-1"],
                    "confirmation_status": "confirmed",
                },
            ],
        }
        catalog_path.write_text(json.dumps(catalog_data))

        result = global_project_context(project="MyProject", home=str(home))

    assert result["resolved"] is True
    assert result["resolution_method"] == "exact_name"
    assert len(result["catalog"]["roots"]) == 1
    assert result["catalog"]["roots"][0]["project_name"] == "MyProject"
    assert len(result["catalog"]["documents"]) == 1


def test_call_tool_dispatches_global_project_context_with_path_home() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        truenex_dir = home / ".truenex-memory"
        truenex_dir.mkdir(parents=True)
        (truenex_dir / "sources.json").write_text(
            json.dumps(
                {
                    "version": "1",
                    "entries": [
                        {
                            "id": "root-path",
                            "source_type": "project_root",
                            "path_or_alias": "/repo/path-project",
                            "project_name": "PathProject",
                            "confirmation_status": "confirmed",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = call_tool(
            "global_project_context",
            {"project": "PathProject", "home": home},
        )

    assert result["resolved"] is True
    assert result["catalog"]["roots"][0]["id"] == "root-path"


def test_global_project_context_validates_limit() -> None:
    from truenex_memory.mcp.tools import global_project_context as gpc

    with pytest.raises(ValueError):
        gpc(project="test", limit=0)

    with pytest.raises(ValueError):
        gpc(project="test", limit=101)

    with pytest.raises(ValueError):
        gpc(project="")

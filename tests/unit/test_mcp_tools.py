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


def test_memory_add_targets_a_different_project_via_catalog() -> None:
    """`project=` scrive altrove dalla cwd della sessione, non nel progetto attivo.

    E' esattamente il caso che ha motivato il parametro: una nota scoperta
    lavorando su A ma pertinente a B, senza dover aprire una sessione con la
    cwd in B solo per salvarla.
    """

    session_root = _workdir("mcp_add_project_session")
    other_root = _workdir("mcp_add_project_other")

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
                            "id": "other-root",
                            "source_type": "project_root",
                            "path_or_alias": str(other_root),
                            "project_name": "OtherProject",
                            "confirmation_status": "confirmed",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = memory_add(
            "Nota pertinente a OtherProject.",
            "note",
            project="OtherProject",
            project_root=session_root,
            home=str(home),
        )

    assert "error" not in result
    assert Path(result["project_root"]).resolve() == other_root.resolve()

    found_elsewhere = memory_search("pertinente a OtherProject", project_root=session_root)
    assert not found_elsewhere["results"], "non deve finire nel progetto della sessione"

    found_target = memory_search("pertinente a OtherProject", project_root=other_root)
    assert found_target["results"]


def test_memory_add_reports_ambiguous_project_with_candidates() -> None:
    """Due radici diverse che condividono solo il nome di cartella.

    Diverso dal caso "stesso project_name in due posti" (che il catalogo
    tratta di proposito come un unico progetto tenuto in piu' copie — vedi
    `_resolve_project`, fase 1): qui i due `project_name` sono distinti, e
    solo il basename del percorso coincide, quindi non c'e' un progetto
    "vero" da preferire.
    """

    session_root = _workdir("mcp_add_project_ambiguous")

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
                            "id": "root-a",
                            "source_type": "project_root",
                            "path_or_alias": "/repo/dup-a/shared",
                            "project_name": "SharedFromA",
                            "confirmation_status": "confirmed",
                        },
                        {
                            "id": "root-b",
                            "source_type": "project_root",
                            "path_or_alias": "/repo/dup-b/shared",
                            "project_name": "SharedFromB",
                            "confirmation_status": "confirmed",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = memory_add(
            "Nota ambigua.", "note", project="shared", project_root=session_root, home=str(home)
        )

    assert "error" in result
    assert len(result["candidates"]) == 2


def test_memory_add_reports_unknown_project() -> None:
    session_root = _workdir("mcp_add_project_unknown")

    with tempfile.TemporaryDirectory() as tmp:
        result = memory_add(
            "Nota per un progetto mai catalogato.",
            "note",
            project="NonEsiste",
            project_root=session_root,
            home=tmp,
        )

    assert "error" in result


def test_global_project_context_validates_limit() -> None:
    from truenex_memory.mcp.tools import global_project_context as gpc

    with pytest.raises(ValueError):
        gpc(project="test", limit=0)

    with pytest.raises(ValueError):
        gpc(project="test", limit=101)

    with pytest.raises(ValueError):
        gpc(project="")

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
        "memory_get",
        "memory_graph",
        "memory_add",
        "memory_resume",
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


def _call(project_root: Path, name: str, arguments: dict) -> dict:
    response = handle_jsonrpc_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        project_root=project_root,
    )
    return json.loads(response["result"]["content"][0]["text"])


def test_every_tool_call_is_logged_for_memory_resume(monkeypatch) -> None:
    """Il dispatcher registra da solo, senza che l'agente chiami task_open.

    Questo e' il punto centrale del fix: la cronologia non dipende dalla
    disciplina di chi chiama, perche' viene scritta dal server stesso a ogni
    `tools/call`, qualunque sia il tool.
    """

    project_root = _workdir("mcp_activity_log")
    # I task vivono nell'archivio HOME globale (vedi memory_resume): un HOME
    # finto isola il test dal vero archivio dell'utente che esegue la suite.
    monkeypatch.setenv("HOME", str(_workdir("mcp_activity_log_home")))
    monkeypatch.setenv("USERPROFILE", str(_workdir("mcp_activity_log_home2")))

    _call(project_root, "memory_add", {"content": "Prima nota", "memory_type": "note"})
    _call(project_root, "memory_search", {"query": "prima nota"})
    recap = _call(project_root, "memory_resume", {})

    tool_names = [entry["tool_name"] for entry in recap["activity"]]
    assert "memory_add" in tool_names
    assert "memory_search" in tool_names
    assert recap["recent_memories"][0]["title"] == "Prima nota"
    assert "coverage" in recap


def test_memory_resume_reports_empty_project_honestly(monkeypatch) -> None:
    """Nessuna chiamata precedente: liste vuote, non un errore.

    E il `coverage` deve restare presente, cosi' un recap vuoto si legge
    come «nessuna attivita' registrata», non come «lo strumento e' rotto».
    """

    monkeypatch.setenv("HOME", str(_workdir("mcp_activity_empty_home")))
    monkeypatch.setenv("USERPROFILE", str(_workdir("mcp_activity_empty_home2")))
    recap = _call(_workdir("mcp_activity_empty"), "memory_resume", {})

    assert recap["activity"] == []
    assert recap["tasks"] == []
    assert recap["recent_memories"] == []
    assert set(recap["coverage"]) == {"activity", "tasks", "recent_memories"}


def test_memory_resume_reads_tasks_from_the_global_store_not_the_local_one(monkeypatch) -> None:
    """Regressione: task_open scrive nell'archivio HOME globale, non in
    quello locale del progetto (`<project_root>/.truenex-memory`).

    Trovato con una domanda vera, non con un test: un `memory_resume` che
    leggeva i task dal db locale del progetto non vedeva MAI un task appena
    aperto nella stessa sessione — solo righe residue di un percorso di
    codice precedente rimaste in quel file. Questo test blocca la
    regressione: un task aperto con `task_open` deve comparire nel recap.
    """

    project_root = _workdir("mcp_resume_tasks_project")
    fake_home = _workdir("mcp_resume_tasks_home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    opened = _call(
        project_root,
        "task_open",
        {"title": "Task aperto in questa sessione", "project": project_root.name},
    )
    recap = _call(project_root, "memory_resume", {})

    assert opened["task_id"] in {t["task_id"] for t in recap["tasks"]}


def test_memory_add_can_target_a_different_project_over_stdio(monkeypatch) -> None:
    """Il parametro `project` di memory_add deve arrivare da un client REALE.

    Prima di questo test lo schema stdio non lo dichiarava e il dispatcher
    non lo inoltrava: un client MCP non poteva usarlo, anche se la funzione
    Python sottostante lo supportava gia'. E' esattamente il tipo di lacuna
    che una prova solo unitaria (sulla funzione, non sul protocollo) lascia
    passare.
    """

    session_root = _workdir("mcp_add_project_stdio_session")
    other_root = _workdir("mcp_add_project_stdio_other")
    fake_home = _workdir("mcp_add_project_stdio_home")
    (fake_home / ".truenex-memory").mkdir(parents=True, exist_ok=True)
    (fake_home / ".truenex-memory" / "sources.json").write_text(
        json.dumps(
            {
                "version": "1",
                "entries": [
                    {
                        "id": "other",
                        "source_type": "project_root",
                        "path_or_alias": str(other_root.resolve()),
                        "project_name": "OtherProj",
                        "confirmation_status": "confirmed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(fake_home.resolve()))
    monkeypatch.setenv("USERPROFILE", str(fake_home.resolve()))

    result = _call(
        session_root,
        "memory_add",
        {"content": "Nota per l'altro progetto", "project": "OtherProj"},
    )

    assert "error" not in result
    assert Path(result["project_root"]).resolve() == other_root.resolve()


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

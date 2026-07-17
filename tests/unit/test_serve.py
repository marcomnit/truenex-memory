"""Tests for FastAPI HTTP endpoints."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from truenex_memory import __version__
from truenex_memory.store.sqlite import connect, initialize_schema


@pytest.fixture
def client(tmp_path: Path):
    """TestClient backed by a temporary SQLite DB (schema v5)."""
    db_path = tmp_path / ".truenex-memory" / "truenex_memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    initialize_schema(conn)
    conn.close()

    os.environ["TRUENEX_PROJECT_ROOT"] = str(tmp_path)
    from truenex_memory.serve import app

    yield TestClient(app, raise_server_exceptions=False)
    os.environ.pop("TRUENEX_PROJECT_ROOT", None)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}


def test_version(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.json() == {"version": __version__, "engine": "multi-tool-v3"}


def test_projects(client):
    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["db_exists"] is True


def test_sources(client):
    r = client.get("/api/sources")
    assert r.status_code == 200
    assert r.json() == []


def test_stats(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["projects"] == 1
    assert data["documents"] == 0
    assert data["chunks"] == 0
    assert data["memory_nodes"] == 0


def test_settings(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert "data_dir" in data
    assert "db_path" in data
    assert "chunk_size" in data
    assert "chunk_overlap" in data
    assert "vector_backend" in data


def test_file_metadata_missing(client):
    r = client.get("/api/file-metadata", params={"document_id": "missing"})
    assert r.status_code == 200
    assert r.json() == {"error": "document not found"}


def test_file_analysis_missing(client):
    r = client.get("/api/file-analysis", params={"file_id": "missing"})
    assert r.status_code == 200
    assert r.json() == {"error": "document not found"}


def test_documents(client):
    r = client.get("/api/documents")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_settings_update(client):
    r = client.post("/api/settings", json={"backend_port": 8001})
    assert r.status_code == 200
    assert r.json()["updated"] is True


def test_memory_add_and_list(client):
    # add
    r = client.post("/api/memory", json={"content": "test note", "memory_type": "note"})
    assert r.status_code == 200
    add_data = r.json()
    assert "id" in add_data

    # list
    r = client.get("/api/memory")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["content"] == "test note"


def test_search(client):
    r = client.post("/api/search", json={"query": "test", "top_k": 5})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_memory_status_update(client):
    # add memory first
    r = client.post("/api/memory", json={"content": "status test", "memory_type": "note"})
    memory_id = r.json()["id"]

    # update status
    r = client.patch(f"/api/memory/{memory_id}/status", json={"status": "obsolete"})
    assert r.status_code == 200
    assert r.json()["status"] == "obsolete"


def test_chat_no_context(client):
    r = client.post("/api/chat", json={
        "query": "something irrelevant",
        "provider": "openai",
        "api_key": "fake",
    })
    # With a fake key we get either 401 (auth error) or 200 with empty context
    assert r.status_code in (200, 401, 502, 503, 504)
    if r.status_code == 200:
        data = r.json()
        assert "Non ho trovato documenti pertinenti" in data["answer"] or data["sources"] == []

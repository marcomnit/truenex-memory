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
    """TestClient backed by a temporary SQLite DB (schema v6)."""
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
    """Un archivio vuoto ha ZERO progetti.

    Prima `projects` era la costante `1`, scritta a mano nell'endpoint, e questo
    test la confermava. Sulla macchina di Marco l'interfaccia mostrava «1
    progetto» mentre nel database ce n'erano cinquantotto. Un numero inventato
    e' peggio di un numero assente: nessuno lo mette in dubbio.
    """

    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["projects"] == 0
    assert data["documents"] == 0
    assert data["chunks"] == 0
    assert data["memory_nodes"] == 0


def test_stats_conta_i_progetti_veri(client, tmp_path):
    """Tre progetti nel registro, tre nella risposta."""

    from truenex_memory.serve import _get_service
    from truenex_memory.store.sqlite import connect

    svc = _get_service()
    svc.repository.initialize()
    with connect(svc.config.db_path) as conn:
        for i, nome in enumerate(("alfa", "beta", "gamma", "alfa")):
            conn.execute(
                "INSERT INTO source_ledger (source_id, source_type,"
                " source_path_or_alias, project_name, status, created_at, updated_at)"
                " VALUES (?, 'document', ?, ?, 'active', '2026-08-24', '2026-08-24')",
                (f"s{i}", f"{nome}/f{i}.md", nome),
            )
        # Una voce non attiva NON conta: mostrarla gonfierebbe il numero con roba
        # che non e' piu' cercabile.
        conn.execute(
            "INSERT INTO source_ledger (source_id, source_type,"
            " source_path_or_alias, project_name, status, created_at, updated_at)"
            " VALUES ('s9', 'document', 'delta/x.md', 'delta', 'skipped',"
            " '2026-08-24', '2026-08-24')"
        )
        conn.commit()

    data = client.get("/api/stats").json()
    assert data["projects"] == 3


def test_stats_dichiara_quale_negozio_ha_aperto(client):
    """Il percorso del database nella risposta.

    `serve` prende il negozio da `<radice>/.truenex-memory/`, e la radice per
    difetto e' la cartella corrente. Avviarlo dentro il repo del sorgente apre
    un database di prova da 14 MB invece dell'archivio da 3,5 GB nella cartella
    utente. E' successo, per un pomeriggio, e non c'era modo di accorgersene
    guardando l'app: sembrava soltanto che ci fosse poca roba, e la poca roba
    non e' un errore riconoscibile.
    """

    negozio = client.get("/api/stats").json()["store"]

    assert negozio["db_path"].endswith("truenex_memory.db")
    assert negozio["project_root"]
    assert isinstance(negozio["db_exists"], bool)
    assert isinstance(negozio["db_size_bytes"], int)


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

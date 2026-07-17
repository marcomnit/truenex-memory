"""Exhaustive tests for /api/search filter behavior.

These tests document the v0.1 filter semantics and expose known
limitations/hard-coded values (e.g. PROJECT_ID == "default").
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from truenex_memory.core.chunker import chunk_text
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.sqlite import connect, initialize_schema


@pytest.fixture
def client_with_data(tmp_path: Path):
    """FastAPI test client backed by a real SQLite DB with sample docs + memories."""
    # Write a sample file to index
    test_file = tmp_path / "auth_doc.md"
    test_file.write_text("authentication logic for user login system", encoding="utf-8")

    # Force the server to use this temp directory and a stable project id
    os.environ["TRUENEX_PROJECT_ROOT"] = str(tmp_path)
    os.environ["TRUENEX_PROJECT_ID"] = "test-project"

    # Initialize the DB schema at the default location
    db_dir = tmp_path / ".truenex-memory"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "truenex_memory.db"
    conn = connect(db_path)
    initialize_schema(conn)
    conn.close()

    # Seed data directly through the repository (no Qdrant/embedder needed)
    repo = MemoryRepository(db_path)
    chunks = chunk_text(test_file.read_text(encoding="utf-8"))
    repo.upsert_document(test_file, "auth_doc.md", chunks)
    repo.add_memory("note about authentication", memory_type="note", status="active")
    repo.add_memory("obsolete authentication idea", memory_type="note", status="obsolete")

    from truenex_memory.serve import app

    yield TestClient(app, raise_server_exceptions=True)

    os.environ.pop("TRUENEX_PROJECT_ROOT", None)
    os.environ.pop("TRUENEX_PROJECT_ID", None)


@pytest.fixture
def auth_results(client_with_data: TestClient) -> list[dict]:
    """Baseline search results for 'authentication' without filters."""
    response = client_with_data.post(
        "/api/search",
        json={"query": "authentication", "top_k": 10},
    )
    assert response.status_code == 200
    return response.json()


class TestBaselineSearch:
    def test_search_without_filters_returns_hits(self, auth_results: list[dict]):
        assert len(auth_results) >= 2, f"expected >=2 hits, got {len(auth_results)}"

    def test_hit_shape(self, auth_results: list[dict]):
        hit = auth_results[0]
        expected_keys = {
            "title",
            "content",
            "source_path",
            "heading_path",
            "memory_type",
            "status",
            "score",
            "source_id",
            "document_id",
            "project",
            "created_at",
        }
        assert expected_keys.issubset(hit.keys())


class TestProjectFilter:
    def test_project_real_name_matches(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"project": "test-project"},
            },
        )
        assert response.status_code == 200
        assert len(response.json()) >= 1

    def test_project_unknown_returns_empty(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"project": "truenex"},
            },
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_project_filter_is_case_insensitive(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"project": "TEST-PROJECT"},
            },
        )
        assert response.status_code == 200
        assert len(response.json()) >= 1

    def test_project_filter_is_substring_match(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"project": "test"},
            },
        )
        assert response.status_code == 200
        assert len(response.json()) >= 1


class TestTypeFilter:
    def test_type_document_chunk_matches_chunks(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"type": "document_chunk"},
            },
        )
        assert response.status_code == 200
        results = response.json()
        assert all(h["memory_type"] == "document_chunk" for h in results)
        assert len(results) >= 1

    def test_type_document_alias_matches_document_chunk(self, client_with_data: TestClient):
        """'document' is accepted as a UX alias for 'document_chunk'."""
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"type": "document"},
            },
        )
        assert response.status_code == 200
        results = response.json()
        assert all(h["memory_type"] == "document_chunk" for h in results)
        assert len(results) >= 1

    def test_type_note_matches_memory_nodes(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"type": "note"},
            },
        )
        assert response.status_code == 200
        results = response.json()
        assert all(h["memory_type"] == "note" for h in results)
        assert len(results) >= 1

    def test_type_code_returns_empty_for_unseeded_type(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"type": "code"},
            },
        )
        assert response.status_code == 200
        assert response.json() == []


class TestStatusFilter:
    def test_status_active_matches(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"status": "active"},
            },
        )
        assert response.status_code == 200
        results = response.json()
        assert all(h["status"] == "active" for h in results)
        assert len(results) >= 1

    def test_status_obsolete_matches_only_obsolete(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"status": "obsolete"},
            },
        )
        assert response.status_code == 200
        results = response.json()
        assert all(h["status"] == "obsolete" for h in results)
        assert len(results) == 1


class TestDateAfterFilter:
    def test_date_after_future_returns_empty(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"date_after": "2099-01-01"},
            },
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_date_after_past_matches(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {"date_after": "2000-01-01"},
            },
        )
        assert response.status_code == 200
        assert len(response.json()) >= 1


class TestCombinedFilters:
    def test_project_and_type_and_status(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {
                    "project": "test-project",
                    "type": "note",
                    "status": "active",
                },
            },
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) >= 1, f"expected >=1, got {len(results)}: {results}"
        assert results[0]["memory_type"] == "note"
        assert results[0]["status"] == "active"

    def test_project_and_type_contradiction_returns_empty(self, client_with_data: TestClient):
        # document_chunk entries are always active, so type=document_chunk + status=obsolete
        # can never match.
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 10,
                "filters": {
                    "project": "test-project",
                    "type": "document_chunk",
                    "status": "obsolete",
                },
            },
        )
        assert response.status_code == 200
        assert response.json() == []


class TestTopKFilteringInteraction:
    """Filters are applied after retrieval, but the backend fetches extra
    candidates so that strict filters still return meaningful results.
    """

    def test_top_k_zero_returns_empty(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={"query": "authentication", "top_k": 0, "filters": {"status": "active"}},
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_strict_filter_on_small_top_k_finds_matches(self, client_with_data: TestClient):
        """Even with top_k=1, the backend fetches more candidates internally."""
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 1,
                "filters": {"type": "note"},
            },
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["memory_type"] == "note"

    def test_filter_results_respect_requested_top_k(self, client_with_data: TestClient):
        response = client_with_data.post(
            "/api/search",
            json={
                "query": "authentication",
                "top_k": 1,
                "filters": {"status": "active"},
            },
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) <= 1
        assert all(h["status"] == "active" for h in results)

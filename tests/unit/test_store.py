"""Tests for the local SQLite memory store."""

import pytest

from truenex_memory.store import SQLiteMemoryStore


def test_store_adds_and_searches_text() -> None:
    with SQLiteMemoryStore(":memory:") as store:
        first = store.add("A deterministic local memory chunk", metadata={"source": "unit"})
        store.add("Another unrelated note")

        results = store.search("local memory")

    assert first.id == 1
    assert len(results) == 1
    assert results[0].text == "A deterministic local memory chunk"
    assert results[0].metadata == {"source": "unit"}


def test_store_search_is_case_insensitive_and_limited() -> None:
    with SQLiteMemoryStore(":memory:") as store:
        store.add("Alpha memory")
        store.add("alpha second memory")
        store.add("beta memory")

        results = store.search("ALPHA", limit=1)

    assert [record.text for record in results] == ["Alpha memory"]


def test_store_validates_inputs() -> None:
    with SQLiteMemoryStore(":memory:") as store:
        with pytest.raises(ValueError, match="text"):
            store.add("")

        with pytest.raises(ValueError, match="limit"):
            store.search("anything", limit=0)


def test_store_creates_base_schema() -> None:
    with SQLiteMemoryStore(":memory:") as store:
        table_names = {
            row[0]
            for row in store._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        assert "memories" in table_names
        assert store.schema_version() == 4

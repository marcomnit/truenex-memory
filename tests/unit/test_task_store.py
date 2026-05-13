"""Unit tests for TaskStore."""
import pytest
from pathlib import Path
from truenex_memory.store.task_store import TaskStore


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "test.db")


def test_task_open_close(store: TaskStore) -> None:
    task_id = store.task_open("Fix login bug", "bugfix", project="AI_Agent")
    assert task_id.startswith("task_")
    record = store.task_close(task_id, human_outcome=1, human_comment="worked")
    assert record.status == "closed"
    assert record.human_outcome == 1


def test_task_close_unrated(store: TaskStore) -> None:
    task_id = store.task_open("Quick query", "query")
    record = store.task_close(task_id)
    assert record.status == "unrated"
    assert record.human_outcome is None


def test_step_add_and_list(store: TaskStore) -> None:
    task_id = store.task_open("Feature X", "feature")
    step_id = store.step_add(task_id, prompt_used="do X", brain_judgment="ok", tokens_used=100, model_used="qwen8b")
    assert step_id.startswith("step_")
    steps = store.step_list(task_id)
    assert len(steps) == 1
    assert steps[0].step_index == 0


def test_step_aggregation_on_close(store: TaskStore) -> None:
    task_id = store.task_open("Refactor Y", "refactor")
    store.step_add(task_id, tokens_used=50, duration_s=1.5)
    store.step_add(task_id, tokens_used=80, duration_s=2.0)
    record = store.task_close(task_id, human_outcome=0)
    assert record.total_tokens == 130
    assert record.total_duration_s == pytest.approx(3.5)


def test_task_list_filter(store: TaskStore) -> None:
    t1 = store.task_open("Task A", "bugfix", project="proj1")
    store.task_open("Task B", "feature", project="proj2")
    store.task_close(t1, human_outcome=1)
    results = store.task_list(status="closed")
    assert any(r.task_id == t1 for r in results)
    assert all(r.status == "closed" for r in results)


def test_invalid_task_type(store: TaskStore) -> None:
    with pytest.raises(ValueError, match="task_type"):
        store.task_open("Bad task", "invalid_type")


def test_invalid_brain_judgment(store: TaskStore) -> None:
    task_id = store.task_open("T", "feature")
    with pytest.raises(ValueError, match="brain_judgment"):
        store.step_add(task_id, brain_judgment="bad")


def test_task_not_found(store: TaskStore) -> None:
    with pytest.raises(LookupError):
        store.task_get("task_nonexistent")


def test_calibration_empty(store: TaskStore) -> None:
    data = store.calibration()
    assert data["verifier_acceptance"] == []
    assert data["brain_human_alignment"] == []


def test_schema_version_bumped(store: TaskStore) -> None:
    from truenex_memory.store.sqlite import connect, initialize_schema, SCHEMA_VERSION
    with connect(store.db_path) as conn:
        initialize_schema(conn)
        row = conn.execute("SELECT version FROM schema_migrations ORDER BY CAST(version AS INTEGER) DESC LIMIT 1").fetchone()
        assert row["version"] == SCHEMA_VERSION == "4"

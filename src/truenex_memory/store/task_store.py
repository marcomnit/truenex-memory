"""CRUD for the adaptive task pipeline tables."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid
from truenex_memory.store.sqlite import connect, initialize_schema

TASK_TYPES = frozenset({"bugfix", "feature", "refactor", "review", "query"})
BRAIN_JUDGMENTS = frozenset({"ok", "needs_revision", "rejected"})
TASK_STATUSES = frozenset({"open", "closed", "unrated"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    title: str
    type: str
    project: str | None
    agent_session_id: str | None
    human_outcome: int | None
    human_comment: str | None
    total_tokens: int | None
    total_duration_s: float | None
    status: str
    created_at: str
    closed_at: str | None


@dataclass(frozen=True)
class TaskStepRecord:
    step_id: str
    task_id: str
    step_index: int
    prompt_used: str | None
    output: str | None
    brain_judgment: str | None
    tokens_used: int | None
    duration_s: float | None
    model_used: str | None
    created_at: str


@dataclass(frozen=True)
class VerifierRoundRecord:
    round_id: str
    task_id: str
    step_id: str | None
    suggestion_type: str
    brain_accepted: bool
    brain_rationale: str | None
    created_at: str


class TaskStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = connect(self.db_path)
        initialize_schema(conn)
        return conn

    def task_open(self, title: str, task_type: str, *, project: str | None = None, agent_session_id: str | None = None) -> str:
        if not title.strip():
            raise ValueError("title cannot be empty")
        if task_type not in TASK_TYPES:
            raise ValueError(f"task_type must be one of {sorted(TASK_TYPES)}")
        task_id = _new_id("task")
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, title, type, project, agent_session_id, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
                (task_id, title.strip(), task_type, project, agent_session_id, now),
            )
            conn.commit()
        return task_id

    def task_close(self, task_id: str, *, human_outcome: int | None = None, human_comment: str | None = None) -> TaskRecord:
        if human_outcome is not None and human_outcome not in (1, 0, -1):
            raise ValueError("human_outcome must be 1, 0, or -1")
        status = "closed" if human_outcome is not None else "unrated"
        now = _now()
        with self._conn() as conn:
            row = conn.execute("SELECT SUM(tokens_used), SUM(duration_s) FROM task_steps WHERE task_id = ?", (task_id,)).fetchone()
            total_tokens = row[0]
            total_duration_s = row[1]
            conn.execute(
                "UPDATE tasks SET status=?, human_outcome=?, human_comment=?, total_tokens=?, total_duration_s=?, closed_at=? WHERE task_id=?",
                (status, human_outcome, human_comment, total_tokens, total_duration_s, now, task_id),
            )
            if conn.execute("SELECT changes()").fetchone()[0] == 0:
                raise LookupError(f"task not found: {task_id!r}")
            conn.commit()
            return self._get_task(conn, task_id)

    def task_get(self, task_id: str) -> TaskRecord:
        with self._conn() as conn:
            return self._get_task(conn, task_id)

    def task_list(self, *, project: str | None = None, status: str | None = None, limit: int = 20) -> list[TaskRecord]:
        if status is not None and status not in TASK_STATUSES:
            raise ValueError(f"status must be one of {sorted(TASK_STATUSES)}")
        with self._conn() as conn:
            clauses: list[str] = []
            args: list[object] = []
            if project is not None:
                clauses.append("project = ?")
                args.append(project)
            if status is not None:
                clauses.append("status = ?")
                args.append(status)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            args.append(limit)
            rows = conn.execute(f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?", args).fetchall()
            return [_task_from_row(row) for row in rows]

    def _get_task(self, conn: sqlite3.Connection, task_id: str) -> TaskRecord:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise LookupError(f"task not found: {task_id!r}")
        return _task_from_row(row)

    def step_add(self, task_id: str, *, prompt_used: str | None = None, output: str | None = None,
                 brain_judgment: str | None = None, tokens_used: int | None = None,
                 duration_s: float | None = None, model_used: str | None = None) -> str:
        if brain_judgment is not None and brain_judgment not in BRAIN_JUDGMENTS:
            raise ValueError(f"brain_judgment must be one of {sorted(BRAIN_JUDGMENTS)}")
        with self._conn() as conn:
            if conn.execute("SELECT task_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone() is None:
                raise LookupError(f"task not found: {task_id!r}")
            idx = conn.execute("SELECT COALESCE(MAX(step_index), -1) + 1 FROM task_steps WHERE task_id = ?", (task_id,)).fetchone()[0]
            step_id = _new_id("step")
            conn.execute(
                "INSERT INTO task_steps (step_id, task_id, step_index, prompt_used, output, brain_judgment, tokens_used, duration_s, model_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (step_id, task_id, idx, prompt_used, output, brain_judgment, tokens_used, duration_s, model_used, _now()),
            )
            conn.commit()
        return step_id

    def step_list(self, task_id: str) -> list[TaskStepRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_index", (task_id,)).fetchall()
            return [_step_from_row(row) for row in rows]

    def verifier_add(self, task_id: str, suggestion_type: str, brain_accepted: bool, *,
                     step_id: str | None = None, brain_rationale: str | None = None) -> str:
        if not suggestion_type.strip():
            raise ValueError("suggestion_type cannot be empty")
        round_id = _new_id("vround")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO verifier_rounds (round_id, task_id, step_id, suggestion_type, brain_accepted, brain_rationale, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (round_id, task_id, step_id, suggestion_type.strip(), 1 if brain_accepted else 0, brain_rationale, _now()),
            )
            conn.commit()
        return round_id

    def calibration(self, *, project: str | None = None) -> dict[str, object]:
        with self._conn() as conn:
            task_filter = ""
            args_vr: list[object] = []
            if project is not None:
                task_filter = "JOIN tasks t ON t.task_id = vr.task_id WHERE t.project = ?"
                args_vr.append(project)
            rows_vr = conn.execute(
                f"SELECT suggestion_type, COUNT(*) AS total, SUM(brain_accepted) AS accepted FROM verifier_rounds vr {task_filter} GROUP BY suggestion_type ORDER BY suggestion_type",
                args_vr,
            ).fetchall()
            verifier_rates = [
                {"suggestion_type": r["suggestion_type"], "total": r["total"], "accepted": r["accepted"],
                 "acceptance_rate": round(r["accepted"] / r["total"], 3) if r["total"] else None}
                for r in rows_vr
            ]
            where_align = "WHERE t.human_outcome IS NOT NULL AND ts.brain_judgment IS NOT NULL"
            args_al: list[object] = []
            if project is not None:
                where_align += " AND t.project = ?"
                args_al.append(project)
            rows_al = conn.execute(
                f"SELECT ts.brain_judgment, t.human_outcome, COUNT(*) AS cnt FROM tasks t JOIN task_steps ts ON ts.task_id = t.task_id {where_align} GROUP BY ts.brain_judgment, t.human_outcome ORDER BY ts.brain_judgment, t.human_outcome",
                args_al,
            ).fetchall()
            alignment = [{"brain_judgment": r["brain_judgment"], "human_outcome": r["human_outcome"], "count": r["cnt"]} for r in rows_al]
            return {"verifier_acceptance": verifier_rates, "brain_human_alignment": alignment}


def _task_from_row(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        task_id=row["task_id"], title=row["title"], type=row["type"],
        project=row["project"], agent_session_id=row["agent_session_id"],
        human_outcome=row["human_outcome"], human_comment=row["human_comment"],
        total_tokens=row["total_tokens"], total_duration_s=row["total_duration_s"],
        status=row["status"], created_at=row["created_at"], closed_at=row["closed_at"],
    )


def _step_from_row(row: sqlite3.Row) -> TaskStepRecord:
    return TaskStepRecord(
        step_id=row["step_id"], task_id=row["task_id"], step_index=row["step_index"],
        prompt_used=row["prompt_used"], output=row["output"], brain_judgment=row["brain_judgment"],
        tokens_used=row["tokens_used"], duration_s=row["duration_s"], model_used=row["model_used"],
        created_at=row["created_at"],
    )

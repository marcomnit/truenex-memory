"""Recursive multi-agent loop engine.

Implements the RecursiveMAS pattern at the orchestration layer:
Truenex Memory acts as the shared latent workspace.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from truenex_memory.core.chunker import content_hash
from truenex_memory.core.memory_service import MemoryService
from truenex_memory.store.task_store import TaskStore


@dataclass(frozen=True)
class RecursivePhase:
    """A single phase inside a recursive loop."""

    name: str
    command: str
    role: str = "agent"


@dataclass
class RecursiveLoopConfig:
    """User-supplied loop configuration."""

    name: str
    phases: list[RecursivePhase]
    max_depth: int = 3
    convergence_strategy: Literal["hash", "last_phase_hash"] = "hash"
    timeout: float | None = 300.0

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("loop name cannot be empty")
        if not self.phases:
            raise ValueError("at least one phase is required")
        names = [p.name for p in self.phases]
        if len(names) != len(set(names)):
            raise ValueError("phase names must be unique")
        if self.max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        if self.convergence_strategy not in ("hash", "last_phase_hash"):
            raise ValueError("convergence_strategy must be 'hash' or 'last_phase_hash'")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be > 0 or None")

    @classmethod
    def from_dict(cls, raw: dict) -> RecursiveLoopConfig:
        phases = [RecursivePhase(**p) for p in raw.get("phases", [])]
        return cls(
            name=raw["name"],
            phases=phases,
            max_depth=raw.get("max_depth", 3),
            convergence_strategy=raw.get("convergence_strategy", "hash"),
            timeout=raw.get("timeout", 300.0),
        )


@dataclass(frozen=True)
class RecursiveRound:
    """A captured round of execution."""

    iteration: int
    phase: str
    output: str
    output_hash: str
    loop_name: str
    created_at: str


@dataclass
class RecursiveLoopReport:
    """Final report for a completed or aborted loop."""

    loop_name: str
    task_id: str | None
    iterations_run: int
    max_depth: int
    converged: bool
    convergence_iteration: int | None
    round_ids: list[str] = field(default_factory=list)
    error: str | None = None
    phase_results: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "loop_name": self.loop_name,
            "task_id": self.task_id,
            "iterations_run": self.iterations_run,
            "max_depth": self.max_depth,
            "converged": self.converged,
            "convergence_iteration": self.convergence_iteration,
            "round_ids": self.round_ids,
            "error": self.error,
            "phase_results": self.phase_results,
        }


class RecursiveLoop:
    """Execute a recursive multi-agent loop.

    Each phase command is run via ``subprocess.run(shell=True)``.  The
    command is responsible for reading/writing external artefacts; the
    orchestrator only captures *stdout* and persists it as a
    ``recursive_round`` memory node.
    """

    def __init__(
        self,
        config: RecursiveLoopConfig,
        memory_service: MemoryService,
        task_store: TaskStore | None = None,
    ) -> None:
        self.config = config
        self.memory = memory_service
        self.task_store = task_store
        self._previous_aggregate_hash: str | None = None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _run_phase(self, phase: RecursivePhase, iteration: int) -> tuple[RecursiveRound, int]:
        result = subprocess.run(
            phase.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.config.timeout,
        )
        output = result.stdout
        if result.returncode != 0 and not output:
            output = result.stderr
        round_obj = RecursiveRound(
            iteration=iteration,
            phase=phase.name,
            output=output,
            output_hash=content_hash(output),
            loop_name=self.config.name,
            created_at=self._now(),
        )
        return round_obj, result.returncode

    def _persist_round(self, round_obj: RecursiveRound) -> str:
        payload = json.dumps(asdict(round_obj), indent=2)
        memory_id = self.memory.add(payload, memory_type="recursive_round")
        return memory_id

    def _aggregate_hash(self, rounds: list[RecursiveRound]) -> str:
        if self.config.convergence_strategy == "last_phase_hash":
            if not rounds:
                return ""
            return rounds[-1].output_hash
        # default "hash": concatenate all hashes in order
        concatenated = "".join(r.output_hash for r in rounds)
        return content_hash(concatenated)

    def run(self) -> RecursiveLoopReport:
        self.config.validate()

        task_id: str | None = None
        if self.task_store is not None:
            task_id = self.task_store.task_open(
                title=f"recursive-loop:{self.config.name}",
                task_type="feature",
                project=self.memory.config.project_root.name,
            )

        round_ids: list[str] = []
        converged = False
        convergence_iteration: int | None = None
        error_msg: str | None = None
        iterations_completed = 0
        phase_results: list[dict] = []

        try:
            for iteration in range(1, self.config.max_depth + 1):
                iterations_completed = iteration
                iteration_rounds: list[RecursiveRound] = []

                for phase in self.config.phases:
                    round_obj, returncode = self._run_phase(phase, iteration)
                    if returncode != 0:
                        raise RuntimeError(
                            f"Phase '{phase.name}' exited with code {returncode}"
                        )
                    iteration_rounds.append(round_obj)
                    memory_id = self._persist_round(round_obj)
                    round_ids.append(memory_id)
                    phase_results.append({
                        "iteration": iteration,
                        "phase": phase.name,
                        "returncode": returncode,
                        "memory_id": memory_id,
                    })

                agg_hash = self._aggregate_hash(iteration_rounds)

                if self._previous_aggregate_hash is not None:
                    if agg_hash == self._previous_aggregate_hash:
                        converged = True
                        convergence_iteration = iteration
                        break

                self._previous_aggregate_hash = agg_hash

        except Exception as exc:
            error_msg = str(exc)

        report = RecursiveLoopReport(
            loop_name=self.config.name,
            task_id=task_id,
            iterations_run=iterations_completed,
            max_depth=self.config.max_depth,
            converged=converged,
            convergence_iteration=convergence_iteration,
            round_ids=round_ids,
            error=error_msg,
            phase_results=phase_results,
        )

        # Persist summary
        summary = json.dumps(report.to_dict(), indent=2)
        self.memory.add(summary, memory_type="recursive_summary")

        if task_id is not None and self.task_store is not None:
            outcome = 1 if converged else (0 if error_msg is None else -1)
            self.task_store.task_close(task_id, human_outcome=outcome)

        return report

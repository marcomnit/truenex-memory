"""Unit tests for the recursive orchestration layer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from truenex_memory.orchestration.recursive_loop import (
    RecursiveLoop,
    RecursiveLoopConfig,
    RecursiveLoopReport,
    RecursivePhase,
)
from truenex_memory.cli.orchestrate_commands import orchestrate_app
from truenex_memory.core.memory_service import MemoryService
from truenex_memory.store.task_store import TaskStore


@pytest.fixture
def mock_memory_service(tmp_path: Path) -> MagicMock:
    service = MagicMock()
    service.config.project_name = "test-proj"
    service.config.db_path = tmp_path / "test.db"
    service.add.return_value = "mem_123"
    return service


@pytest.fixture
def mock_task_store() -> MagicMock:
    store = MagicMock()
    store.task_open.return_value = "task_456"
    return store


class TestRecursiveLoopConfig:
    def test_from_dict_roundtrip(self) -> None:
        raw = {
            "name": "demo",
            "phases": [
                {"name": "plan", "command": "echo plan", "role": "architect"},
                {"name": "build", "command": "echo build"},
            ],
            "max_depth": 2,
            "convergence_strategy": "last_phase_hash",
            "timeout": 60.0,
        }
        config = RecursiveLoopConfig.from_dict(raw)
        assert config.name == "demo"
        assert len(config.phases) == 2
        assert config.phases[1].role == "agent"
        assert config.max_depth == 2
        assert config.timeout == 60.0

    def test_validate_empty_name(self) -> None:
        config = RecursiveLoopConfig(name="", phases=[RecursivePhase("a", "echo")])
        with pytest.raises(ValueError, match="cannot be empty"):
            config.validate()

    def test_validate_duplicate_names(self) -> None:
        config = RecursiveLoopConfig(
            name="x",
            phases=[
                RecursivePhase("a", "echo"),
                RecursivePhase("a", "echo"),
            ],
        )
        with pytest.raises(ValueError, match="unique"):
            config.validate()


class TestRecursiveLoopRun:
    def test_converges_on_second_iteration(
        self,
        mock_memory_service: MagicMock,
        mock_task_store: MagicMock,
    ) -> None:
        config = RecursiveLoopConfig(
            name="test-loop",
            phases=[RecursivePhase("gen", "echo stable")],
            max_depth=5,
            convergence_strategy="hash",
        )
        loop = RecursiveLoop(config, mock_memory_service, task_store=mock_task_store)

        with patch("truenex_memory.orchestration.recursive_loop.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args="", returncode=0, stdout="stable_output",
            )
            report = loop.run()

        assert report.converged is True
        assert report.convergence_iteration == 2
        assert report.iterations_run == 2
        assert report.max_depth == 5
        assert report.error is None
        mock_task_store.task_open.assert_called_once()
        mock_task_store.task_close.assert_called_once()

    def test_runs_max_depth_without_convergence(
        self,
        mock_memory_service: MagicMock,
        mock_task_store: MagicMock,
    ) -> None:
        config = RecursiveLoopConfig(
            name="test-loop",
            phases=[RecursivePhase("gen", "echo")],
            max_depth=3,
            convergence_strategy="hash",
        )
        loop = RecursiveLoop(config, mock_memory_service, task_store=mock_task_store)

        outputs = ["a", "b", "c"]
        call_index = 0

        def fake_run(*args, **kwargs):
            nonlocal call_index
            out = outputs[call_index % len(outputs)]
            call_index += 1
            return subprocess.CompletedProcess(args="", returncode=0, stdout=out)

        with patch("truenex_memory.orchestration.recursive_loop.subprocess.run", side_effect=fake_run):
            report = loop.run()

        assert report.converged is False
        assert report.iterations_run == 3
        assert report.convergence_iteration is None
        assert len(report.round_ids) == 3
        assert len(report.phase_results) == 3

    def test_task_closed_on_error(
        self,
        mock_memory_service: MagicMock,
        mock_task_store: MagicMock,
    ) -> None:
        config = RecursiveLoopConfig(
            name="fail-loop",
            phases=[RecursivePhase("gen", "false")],
            max_depth=2,
        )
        loop = RecursiveLoop(config, mock_memory_service, task_store=mock_task_store)

        def fake_run(*args, **kwargs):
            raise RuntimeError("boom")

        with patch("truenex_memory.orchestration.recursive_loop.subprocess.run", side_effect=fake_run):
            report = loop.run()

        assert report.error == "boom"
        assert report.converged is False
        assert report.iterations_run == 1  # iteration started before exception
        mock_task_store.task_close.assert_called_once()
        args, kwargs = mock_task_store.task_close.call_args
        assert kwargs["human_outcome"] == -1

    def test_last_phase_hash_strategy(
        self,
        mock_memory_service: MagicMock,
        mock_task_store: MagicMock,
    ) -> None:
        config = RecursiveLoopConfig(
            name="test-loop",
            phases=[
                RecursivePhase("plan", "echo plan"),
                RecursivePhase("build", "echo stable"),
            ],
            max_depth=3,
            convergence_strategy="last_phase_hash",
        )
        loop = RecursiveLoop(config, mock_memory_service, task_store=mock_task_store)

        with patch("truenex_memory.orchestration.recursive_loop.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args="", returncode=0, stdout="stable_output",
            )
            report = loop.run()

        assert report.converged is True
        assert report.convergence_iteration == 2
        assert report.iterations_run == 2

    def test_multi_phase_no_convergence(
        self,
        mock_memory_service: MagicMock,
        mock_task_store: MagicMock,
    ) -> None:
        config = RecursiveLoopConfig(
            name="test-loop",
            phases=[
                RecursivePhase("plan", "echo plan"),
                RecursivePhase("build", "echo build"),
            ],
            max_depth=2,
            convergence_strategy="hash",
        )
        loop = RecursiveLoop(config, mock_memory_service, task_store=mock_task_store)

        outputs = ["p1", "b1", "p2", "b2"]
        call_index = 0

        def fake_run(*args, **kwargs):
            nonlocal call_index
            out = outputs[call_index % len(outputs)]
            call_index += 1
            return subprocess.CompletedProcess(args="", returncode=0, stdout=out)

        with patch("truenex_memory.orchestration.recursive_loop.subprocess.run", side_effect=fake_run):
            report = loop.run()

        assert report.converged is False
        assert report.iterations_run == 2
        assert len(report.round_ids) == 4
        assert len(report.phase_results) == 4

    def test_error_mid_loop_reports_correct_iteration(
        self,
        mock_memory_service: MagicMock,
        mock_task_store: MagicMock,
    ) -> None:
        config = RecursiveLoopConfig(
            name="test-loop",
            phases=[
                RecursivePhase("plan", "echo plan"),
                RecursivePhase("build", "echo build"),
            ],
            max_depth=5,
        )
        loop = RecursiveLoop(config, mock_memory_service, task_store=mock_task_store)

        call_index = 0

        def fake_run(*args, **kwargs):
            nonlocal call_index
            call_index += 1
            if call_index == 3:
                raise RuntimeError("mid-loop failure")
            return subprocess.CompletedProcess(args="", returncode=0, stdout=f"out_{call_index}")

        with patch("truenex_memory.orchestration.recursive_loop.subprocess.run", side_effect=fake_run):
            report = loop.run()

        assert report.error == "mid-loop failure"
        assert report.iterations_run == 2
        assert report.converged is False

    def test_non_zero_phase_returncode_stops_loop(
        self,
        mock_memory_service: MagicMock,
        mock_task_store: MagicMock,
    ) -> None:
        config = RecursiveLoopConfig(
            name="test-loop",
            phases=[RecursivePhase("gen", "false")],
            max_depth=3,
        )
        loop = RecursiveLoop(config, mock_memory_service, task_store=mock_task_store)

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args="", returncode=1, stdout="fail")

        with patch("truenex_memory.orchestration.recursive_loop.subprocess.run", side_effect=fake_run):
            report = loop.run()

        assert report.error is not None
        assert "exited with code 1" in report.error
        assert report.converged is False
        assert report.iterations_run == 1
        mock_task_store.task_close.assert_called_once()
        args, kwargs = mock_task_store.task_close.call_args
        assert kwargs["human_outcome"] == -1

    def test_no_task_store_tracking(self, mock_memory_service: MagicMock) -> None:
        config = RecursiveLoopConfig(
            name="test-loop",
            phases=[RecursivePhase("gen", "echo ok")],
            max_depth=1,
        )
        loop = RecursiveLoop(config, mock_memory_service, task_store=None)

        with patch("truenex_memory.orchestration.recursive_loop.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args="", returncode=0, stdout="ok",
            )
            report = loop.run()

        assert report.task_id is None
        assert report.iterations_run == 1
        assert report.converged is False
        mock_memory_service.add.assert_called()


class TestOrchestrateCLI:
    def test_converge_check_ok(self) -> None:
        runner = CliRunner()
        with patch("truenex_memory.cli.orchestrate_commands._memory_service") as mock_mem:
            svc = MagicMock()
            left_node = MagicMock()
            left_node.content = "same"
            right_node = MagicMock()
            right_node.content = "same"
            svc.get_memory_node.side_effect = [left_node, right_node]
            mock_mem.return_value = svc
            result = runner.invoke(orchestrate_app, ["converge-check", "mem_a", "mem_b"])
        assert result.exit_code == 0
        assert "equal=True" in result.output

    def test_converge_check_ko(self) -> None:
        runner = CliRunner()
        with patch("truenex_memory.cli.orchestrate_commands._memory_service") as mock_mem:
            svc = MagicMock()
            left_node = MagicMock()
            left_node.content = "a"
            right_node = MagicMock()
            right_node.content = "b"
            svc.get_memory_node.side_effect = [left_node, right_node]
            mock_mem.return_value = svc
            result = runner.invoke(orchestrate_app, ["converge-check", "mem_a", "mem_b"])
        assert result.exit_code == 1
        assert "equal=False" in result.output

    def test_converge_check_left_not_found(self) -> None:
        runner = CliRunner()
        with patch("truenex_memory.cli.orchestrate_commands._memory_service") as mock_mem:
            svc = MagicMock()
            svc.get_memory_node.return_value = None
            mock_mem.return_value = svc
            result = runner.invoke(orchestrate_app, ["converge-check", "mem_a", "mem_b"])
        assert result.exit_code == 1
        assert "left ID not found" in result.output

    def test_run_command_invalid_config(self, tmp_path: Path) -> None:
        runner = CliRunner()
        config_path = tmp_path / "bad.json"
        config_path.write_text("not json")
        result = runner.invoke(orchestrate_app, ["run", str(config_path)])
        assert result.exit_code == 1
        assert "invalid config file" in result.output

    def test_run_command_invalid_config_semantic(self, tmp_path: Path) -> None:
        runner = CliRunner()
        config = {
            "name": "",
            "phases": [{"name": "echo", "command": "echo hello"}],
            "max_depth": 1,
        }
        config_path = tmp_path / "loop.json"
        config_path.write_text(json.dumps(config))
        result = runner.invoke(orchestrate_app, ["run", str(config_path)])
        assert result.exit_code == 1
        assert "invalid config file" in result.output

    def test_run_with_real_memory_service(self, tmp_path: Path) -> None:
        memory = MemoryService(tmp_path)
        store = TaskStore(memory.config.db_path)
        config = RecursiveLoopConfig(
            name="real-loop",
            phases=[RecursivePhase("echo", "echo hello")],
            max_depth=1,
            timeout=5.0,
        )
        loop = RecursiveLoop(config, memory, task_store=store)
        with patch("truenex_memory.orchestration.recursive_loop.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args="", returncode=0, stdout="ok",
            )
            report = loop.run()
        assert report.iterations_run == 1
        assert report.task_id is not None
        assert report.error is None
        assert len(report.round_ids) == 1

    def test_run_command_success(self, tmp_path: Path) -> None:
        runner = CliRunner()
        config = {
            "name": "cli-test",
            "phases": [{"name": "echo", "command": "echo hello"}],
            "max_depth": 1,
        }
        config_path = tmp_path / "loop.json"
        config_path.write_text(json.dumps(config))
        with patch("truenex_memory.cli.orchestrate_commands._memory_service") as mock_mem:
            svc = MagicMock()
            svc.config.db_path = tmp_path / "test.db"
            svc.config.project_name = "test"
            mock_mem.return_value = svc
            report = RecursiveLoopReport(
                loop_name="cli-test",
                task_id=None,
                iterations_run=1,
                max_depth=1,
                converged=False,
                convergence_iteration=None,
                round_ids=["mem_1"],
            )
            with patch("truenex_memory.cli.orchestrate_commands.RecursiveLoop") as mock_loop_cls:
                mock_loop = MagicMock()
                mock_loop.run.return_value = report
                mock_loop_cls.return_value = mock_loop
                result = runner.invoke(orchestrate_app, ["run", str(config_path)])
        assert result.exit_code == 3  # no convergence with max_depth=1
        assert "cli-test" in result.output

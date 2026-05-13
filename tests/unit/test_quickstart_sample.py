"""Quickstart tests against the checked-in sample project."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

from typer.testing import CliRunner

from truenex_memory.cli.main import app


runner = CliRunner()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _workdir(name: str) -> Path:
    path = _repo_root() / "tests" / "unit" / f"task_work_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _copy_sample_project(workdir: Path) -> Path:
    source = _repo_root() / "examples" / "sample_project"
    target = workdir / "examples" / "sample_project"
    shutil.copytree(source, target)
    return target


def test_readme_quickstart_cli_flow_indexes_sample_project() -> None:
    workdir = _workdir("quickstart")
    _copy_sample_project(workdir)

    with _cwd(workdir):
        doctor = runner.invoke(app, ["doctor", "--privacy"])
        init = runner.invoke(app, ["init"])
        add = runner.invoke(
            app,
            ["add", "We use SQLite for local metadata", "--type", "decision"],
        )
        index = runner.invoke(app, ["index", "examples/sample_project"])
        search = runner.invoke(
            app,
            ["search", "which vector database is planned?", "--top-k", "3", "--json"],
        )
        export = runner.invoke(app, ["export", "--output", "memory-export.json"])

    assert doctor.exit_code == 0
    assert '"cloud_enabled": false' in doctor.stdout
    assert init.exit_code == 0
    assert add.exit_code == 0
    assert "mem_" in add.stdout
    assert index.exit_code == 0
    assert "Indexed 3 file(s)" in index.stdout
    assert export.exit_code == 0
    assert (workdir / "memory-export.json").exists()

    payload = json.loads(search.stdout)
    assert payload["results"]
    assert any("Qdrant" in result["content"] for result in payload["results"])
    assert all(result["source_path"] for result in payload["results"])
    assert all(result["score"] > 0 for result in payload["results"])


def test_sample_project_search_returns_expected_sources_and_headings() -> None:
    workdir = _workdir("sample_sources")
    _copy_sample_project(workdir)

    with _cwd(workdir):
        assert runner.invoke(app, ["init"]).exit_code == 0
        assert runner.invoke(app, ["index", "examples/sample_project"]).exit_code == 0
        response = runner.invoke(
            app,
            ["search", "MCP stdio transport", "--top-k", "5", "--json"],
        )

    assert response.exit_code == 0
    payload = json.loads(response.stdout)
    results = payload["results"]
    assert results
    source_paths = [result["source_path"].replace("\\", "/") for result in results]
    assert any(source_path.endswith("docs/decisions.md") for source_path in source_paths)
    assert any(
        result["heading_path"] == "Decisions > ADR-003: Use MCP stdio For Agents"
        for result in results
    )
    assert all(isinstance(result["score"], float) for result in results)

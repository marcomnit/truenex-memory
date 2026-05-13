"""End-to-end clean install tests for the CLI quickstart."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


@pytest.mark.e2e
def test_clean_install_cli_quickstart() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workdir = repo_root / "tests" / "e2e" / f"task_work_install_{uuid.uuid4().hex}"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    project_dir = workdir / "project"
    shutil.copytree(repo_root / "examples", project_dir / "examples")

    venv_dir = workdir / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    python = venv_dir / "Scripts" / "python.exe"

    subprocess.run(
        [str(python), "-m", "pip", "install", "-e", f"{repo_root}[dev]"],
        check=True,
        cwd=workdir,
        capture_output=True,
        text=True,
    )

    def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(python), "-m", "truenex_memory.cli.main", *args],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    assert "Local-first memory layer" in run_cli("--help").stdout
    doctor = run_cli("doctor", "--privacy")
    assert '"cloud_enabled": false' in doctor.stdout
    assert "Initialized" in run_cli("init").stdout
    assert "mem_" in run_cli("add", "We use SQLite for local metadata", "--type", "decision").stdout
    assert "Indexed 3 file(s)" in run_cli("index", "examples/sample_project").stdout

    search = run_cli("search", "which vector database is planned?", "--top-k", "3", "--json")
    payload = json.loads(search.stdout)
    assert payload["results"]
    assert any("Qdrant" in result["content"] for result in payload["results"])
    assert all(result["source_path"] for result in payload["results"])
    assert all(result["score"] > 0 for result in payload["results"])

    assert "Exported" in run_cli("export", "--output", "memory-export.json").stdout
    assert (project_dir / "memory-export.json").exists()

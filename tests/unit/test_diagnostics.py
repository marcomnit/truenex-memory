"""Test local diagnostics."""

import shutil
from pathlib import Path

from truenex_memory.diagnostics import run_diagnostics


def _workdir(name: str) -> Path:
    path = Path("tests/unit/.task3_work") / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def test_run_diagnostics_reports_local_checks() -> None:
    path = _workdir("diagnostics")
    report = run_diagnostics(path)

    assert report["status"] == "ok"
    assert report["base_path"] == str(path)
    assert {check["name"] for check in report["checks"]} >= {
        "python_version",
        "package_import",
        "base_path_exists",
        "base_path_writable",
    }

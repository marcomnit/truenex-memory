"""Focused coverage for the Git Bridge command handlers."""

from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

import pytest
import typer

from truenex_memory.cli import git_commands as commands


def _completed(*, stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.fixture
def git_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    sync = tmp_path / ".truenex-memory" / "sync"
    sync.mkdir(parents=True)
    paths = {
        "sync": sync,
        "db": tmp_path / ".truenex-memory" / "truenex_memory.db",
        "memory": sync / "memory.json",
        "gzip": sync / "memory.json.gz",
        "manifest": sync / "manifest.json",
    }
    monkeypatch.setattr(commands, "check_license", lambda *args, **kwargs: None)
    monkeypatch.setattr(commands, "_sync_dir", lambda root: paths["sync"])
    monkeypatch.setattr(commands, "_db_path", lambda root: paths["db"])
    monkeypatch.setattr(commands, "_memory_json_path", lambda root: paths["memory"])
    monkeypatch.setattr(commands, "_memory_gz_path", lambda root: paths["gzip"])
    monkeypatch.setattr(commands, "_manifest_path", lambda root: paths["manifest"])
    return paths


def test_git_init_creates_repository_files_and_remote(
    tmp_path: Path,
    git_environment: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(commands, "_repo_exists", lambda root: False)

    def fake_git(cwd: Path, *args: str, **kwargs):
        calls.append(args)
        return _completed()

    monkeypatch.setattr(commands, "_run_git", fake_git)

    commands.git_init(
        remote="backup",
        url="git@example.invalid:memory.git",
        json_out=True,
        project_root=tmp_path,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["remote_added"]["name"] == "backup"
    assert git_environment["sync"].joinpath(".gitignore").read_text(encoding="utf-8") == (
        "*.db\n*.db-journal\nmemory.json\n"
    )
    assert ("init",) in calls
    assert ("remote", "add", "backup", "git@example.invalid:memory.git") in calls


def test_git_init_existing_repo_reports_skip(
    tmp_path: Path,
    git_environment: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(commands, "_repo_exists", lambda root: True)

    with pytest.raises(typer.Exit) as exc:
        commands.git_init(json_out=True, project_root=tmp_path)

    assert exc.value.exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "skipped"


def test_push_dry_run_resolves_branch_and_describes_actions(
    tmp_path: Path,
    git_environment: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(commands, "_ensure_repo", lambda root: None)
    monkeypatch.setattr(
        commands,
        "_run_git",
        lambda cwd, *args, **kwargs: _completed(stdout="feature/sync\n"),
    )

    with pytest.raises(typer.Exit) as exc:
        commands.git_push(
            remote="origin",
            branch=None,
            message="sync test",
            json_out=True,
            project_root=tmp_path,
            dry_run=True,
        )

    payload = json.loads(capsys.readouterr().out)
    assert exc.value.exit_code == 0
    assert payload["branch"] == "feature/sync"
    assert payload["status"] == "dry_run"
    assert payload["steps"][-1] == "git push origin feature/sync"


def test_pull_dry_run_falls_back_to_main_branch(
    tmp_path: Path,
    git_environment: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(commands, "_ensure_repo", lambda root: None)
    monkeypatch.setattr(
        commands,
        "_run_git",
        lambda cwd, *args, **kwargs: _completed(stdout=""),
    )

    with pytest.raises(typer.Exit) as exc:
        commands.git_pull(
            remote="upstream",
            branch=None,
            json_out=True,
            project_root=tmp_path,
            dry_run=True,
        )

    payload = json.loads(capsys.readouterr().out)
    assert exc.value.exit_code == 0
    assert payload["branch"] == "main"
    assert payload["steps"][0] == "git pull upstream main"


def test_status_reads_compressed_export_manifest_and_dirty_state(
    tmp_path: Path,
    git_environment: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with gzip.open(git_environment["gzip"], "wb") as handle:
        handle.write(json.dumps({"memory_nodes": [{"id": "1"}, {"id": "2"}]}).encode())
    git_environment["manifest"].write_text('{"version":"1"}', encoding="utf-8")
    monkeypatch.setattr(commands, "_repo_exists", lambda root: True)

    def fake_git(cwd: Path, *args: str, **kwargs):
        if args == ("branch", "--show-current"):
            return _completed(stdout="main\n")
        return _completed(stdout=" M manifest.json\n")

    monkeypatch.setattr(commands, "_run_git", fake_git)

    commands.git_status(short=False, json_out=True, project_root=tmp_path)

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["db_nodes"] == 0
    assert payload["memory_json_nodes"] == 2
    assert payload["manifest_hash"]
    assert payload["branch"] == "main"
    assert payload["dirty"] is True


def test_remote_commands_parse_and_report_results(
    tmp_path: Path,
    git_environment: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(commands, "_ensure_repo", lambda root: None)

    def fake_git(cwd: Path, *args: str, **kwargs):
        if args == ("remote", "-v"):
            return _completed(
                stdout=(
                    "origin https://example.invalid/repo.git (fetch)\n"
                    "origin https://example.invalid/repo.git (push)\n"
                )
            )
        if args == ("remote", "show", "origin"):
            return _completed(stdout="* remote origin\n  HEAD branch: main\n")
        return _completed()

    monkeypatch.setattr(commands, "_run_git", fake_git)

    commands.remote_add("backup", "git@example.invalid:backup.git", True, tmp_path)
    assert json.loads(capsys.readouterr().out)["status"] == "ok"

    commands.remote_remove("backup", True, tmp_path)
    assert json.loads(capsys.readouterr().out)["status"] == "ok"

    commands.remote_list(True, tmp_path)
    listed = json.loads(capsys.readouterr().out)
    assert [entry["type"] for entry in listed["remotes"]["origin"]] == ["fetch", "push"]

    commands.remote_show("origin", True, tmp_path)
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "ok"
    assert "HEAD branch: main" in shown["output"]


def test_remote_show_missing_remote_returns_structured_error(
    tmp_path: Path,
    git_environment: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(commands, "_ensure_repo", lambda root: None)
    monkeypatch.setattr(
        commands,
        "_run_git",
        lambda cwd, *args, **kwargs: _completed(stderr="missing", returncode=2),
    )

    with pytest.raises(typer.Exit) as exc:
        commands.remote_show("missing", True, tmp_path)

    assert exc.value.exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["stderr"] == "missing"

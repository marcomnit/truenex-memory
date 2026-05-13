"""Unit tests for agent discovery."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from contextlib import contextmanager
from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from tests.unit.cli_output import plain_cli_output
from truenex_memory.cli.main import app
from truenex_memory.discovery.agent_discovery import (
    AgentRoot,
    CandidateDocument,
    CandidateProject,
    DiscoveryReport,
    ServerAlias,
    MAX_FILE_READ_CHARS,
    MAX_TEXTS_PER_JSONL_FILE,
    _bounded_read_text,
    _extract_text_from_jsonl,
    _find_paths_in_text,
    _find_ssh_aliases,
    _find_doc_paths,
    _safe_is_dir,
    _safe_exists,
    _looks_like_project_root_path,
    _project_root_from_path,
    _clean_doc_candidate,
    _looks_like_server_alias,
    _deduplicate_projects,
    _deduplicate_documents,
    _deduplicate_servers,
    _score_project,
    _score_document,
    _score_server,
    discover_from_agents,
    format_report,
)

runner = CliRunner()


# ── helpers ───────────────────────────────────────────────────────────

def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parents[1] / "unit" / f"task_work_{name}_{uuid.uuid4().hex}"
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


def _setup_fake_home(home: Path) -> None:
    """Create a fake home directory with .codex and .claude structures."""
    # Codex sessions
    codex_sessions = home / ".codex" / "sessions"
    codex_sessions.mkdir(parents=True)
    (codex_sessions / "session1.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {
            "id": "sess-1", "cwd": "D:\\Project_sw\\ProjectPy\\truenex-memory",
            "model": "gpt-5.5",
        }}) + "\n" +
        json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Work on example-core. Connect via ssh example-core and check the database."}],
        }}) + "\n" +
        json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "I'll connect to example-core via SSH. The project is at /opt/example-app on the server. See /opt/example-app/README.md for setup."}],
        }}) + "\n",
        encoding="utf-8",
    )
    (home / ".codex" / "history.jsonl").write_text(
        json.dumps({
            "session_id": "hist-1",
            "text": "Yesterday we worked on /srv/history-project and used ssh history-core",
        }) + "\n",
        encoding="utf-8",
    )
    (codex_sessions / "session2.jsonl").write_text(
        json.dumps({"type": "user", "message": {
            "role": "user", "content": "Fix the bug in C:\\Users\\dev\\Documents\\MyApp. Check MyApp/guide.md for structure."
        }}) + "\n" +
        json.dumps({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "Looking at the code now. Also, ssh root@example-engine-host to check the deployment."}]
        }}) + "\n",
        encoding="utf-8",
    )

    # Codex memories
    codex_memories = home / ".codex" / "memories"
    codex_memories.mkdir(parents=True)
    (codex_memories / "memory.json").write_text(
        json.dumps([
            {"type": "memory", "content": "Project example-engine at C:\\Users\\dev\\OneDrive\\SOFWARE\\example-engine"},
            {"type": "note", "content": "Useful docs: /home/dev/projects/config.toml, server-notes.yaml"},
        ]) + "\n",
        encoding="utf-8",
    )

    # Claude projects
    claude_projects = home / ".claude" / "projects"
    claude_projects.mkdir(parents=True)
    (claude_projects / "project1.md").write_text(
        "# Project: sample-agent\n"
        "Root: D:\\Project_sw\\ProjectPy\\sample-agent\n"
        "Server: ssh example-core\n"
        "Docs: design.md, implementation.yaml\n",
        encoding="utf-8",
    )
    (claude_projects / "project2.json").write_text(
        json.dumps({"project": "truenex-memory", "root": "/home/dev/projects/truenex-memory"}),
        encoding="utf-8",
    )

    # Claude commands
    claude_commands = home / ".claude" / "commands"
    claude_commands.mkdir(parents=True)
    (claude_commands / "custom-command.md").write_text(
        "# Custom Command\n"
        "Connect to ssh example-engine-host and run /opt/example-app/deploy.sh\n"
        "Config at /opt/example-app/config/settings.toml\n",
        encoding="utf-8",
    )
    (home / ".claude" / "history.jsonl").write_text(
        json.dumps({
            "text": "Claude history mentions C:\\Users\\dev\\ClaudeHistoryProject and ssh claude-history-server",
        }) + "\n",
        encoding="utf-8",
    )

    # Claude skills
    claude_skills = home / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    (claude_skills / "truenex").mkdir()
    (claude_skills / "truenex" / "SKILL.md").write_text(
        "---\nname: truenex\n---\n# Truenex\nServer: /opt/example-app, ssh example-core\n",
        encoding="utf-8",
    )
    (claude_skills / "truenex-engine").mkdir()
    (claude_skills / "truenex-engine" / "SKILL.md").write_text(
        "---\nname: truenex-engine\n---\n# Engine\nPath: /home/dev/example-engine\n",
        encoding="utf-8",
    )


# ── low-level extractors ──────────────────────────────────────────────

class TestPathExtraction:
    def test_find_windows_absolute_paths(self) -> None:
        text = "Project at C:\\Users\\dev\\Projects\\MyApp and D:\\Work\\OtherProject"
        paths = _find_paths_in_text(text)
        assert any("C:\\Users\\dev\\Projects\\MyApp" in p for p in paths)
        assert any("D:\\Work\\OtherProject" in p for p in paths)

    def test_find_unix_absolute_paths(self) -> None:
        text = "Deploy to /opt/example-app/config and see /home/dev/projects/readme.md"
        paths = _find_paths_in_text(text)
        assert any("/opt/example-app/config" in p for p in paths)
        assert any("/home/dev/projects/readme.md" in p for p in paths)

    def test_find_mixed_paths(self) -> None:
        text = "Windows: C:\\Users\\dev\\App, Unix: /var/log/app"
        paths = _find_paths_in_text(text)
        assert len(paths) >= 2

    def test_safe_path_helpers_swallow_os_errors(self, monkeypatch) -> None:
        def raise_os_error(self) -> bool:
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "is_dir", raise_os_error)
        monkeypatch.setattr(Path, "exists", raise_os_error)

        assert _safe_is_dir(Path("C:\\blocked")) is False
        assert _safe_exists(Path("C:\\blocked")) is False

    def test_project_root_filter_rejects_api_routes(self) -> None:
        assert _looks_like_project_root_path("/api/v1/admin/users") is False
        assert _looks_like_project_root_path("/documents/{id}/process") is False
        assert _looks_like_project_root_path("/home/dev/") is False
        assert _looks_like_project_root_path("/opt/example-app") is True
        assert _looks_like_project_root_path("D:\\Project_sw\\ProjectPy\\sample-agent") is True

    def test_project_root_inference_collapses_deep_paths(self) -> None:
        assert (
            _project_root_from_path("/home/dev/AI_ApiFineTune_Chat/.venv/bin/celery")
            == "/home/dev/AI_ApiFineTune_Chat"
        )
        assert (
            _project_root_from_path("D:\\Project_sw\\ProjectPy\\sample-agent\\docs\\README.md")
            == "D:\\Project_sw\\ProjectPy\\sample-agent"
        )
        assert (
            _project_root_from_path("C:\\Users\\dev\\Documents\\MyApp\\README.md")
            == "C:\\Users\\dev\\Documents\\MyApp"
        )
        assert _project_root_from_path("C:\\Windows\\System32\\WindowsPowerShell\\v1.0") is None
        assert _project_root_from_path("C:\\Users\\dev\\.ssh\\id_rsa") is None
        assert _project_root_from_path("C:\\tmp\\some-temp-file.txt") is None
        assert _project_root_from_path("C:\\Users\\dev\\.codex\\skills\\x\\SKILL.md") is None
        assert _project_root_from_path("/api/v1/admin/users") is None
        assert _project_root_from_path("s:\\github.com\\example\\repo") is None
        assert _project_root_from_path("C:\\Users\\dev\\D:\\Project_sw\\ProjectPy\\sample-agent") is None


class TestSSHExtraction:
    def test_simple_ssh_alias(self) -> None:
        aliases = _find_ssh_aliases("ssh example-core and check")
        assert "example-core" in aliases

    def test_ssh_root_at(self) -> None:
        aliases = _find_ssh_aliases("connect via ssh root@production-server now")
        assert "production-server" in aliases

    def test_ssh_user_at(self) -> None:
        aliases = _find_ssh_aliases("ssh admin@staging.example.com to deploy")
        assert "staging.example.com" in aliases

    def test_multiple_ssh_aliases(self) -> None:
        aliases = _find_ssh_aliases(
            "Use ssh example-core for DB and ssh example-engine-host for web"
        )
        assert "example-core" in aliases
        assert "example-engine-host" in aliases

    def test_no_ssh_aliases(self) -> None:
        aliases = _find_ssh_aliases("No SSH references here")
        assert aliases == []

    def test_filters_ssh_prose_words(self) -> None:
        aliases = _find_ssh_aliases("SSH alias and SSH command are mentioned in prose")
        assert aliases == []

    def test_server_alias_shape(self) -> None:
        assert _looks_like_server_alias("example-core") is True
        assert _looks_like_server_alias("65.108.225.186") is True
        assert _looks_like_server_alias("example-engine-host") is True
        assert _looks_like_server_alias("alias") is False
        assert _looks_like_server_alias("read-only") is False
        assert _find_ssh_aliases("ssh example-ec208456\n") == ["example-ec208456"]

    def test_filters_ssh_options(self) -> None:
        aliases = _find_ssh_aliases("ssh -i key.pem ssh -p 22 ssh -v")
        assert "-i" not in aliases
        assert "-p" not in aliases
        assert "-v" not in aliases


class TestDocExtraction:
    def test_find_markdown_files(self) -> None:
        docs = _find_doc_paths("See README.md and guide.md for instructions")
        assert any("README.md" in d for d in docs)
        assert not any("guide.md" in d for d in docs)

    def test_find_yaml_toml_files(self) -> None:
        docs = _find_doc_paths("Config: config/config.toml, ops/server-notes.yaml, deploy/deploy.yml")
        assert any("config/config.toml" in d for d in docs)
        assert any("ops/server-notes.yaml" in d for d in docs)
        assert any("deploy/deploy.yml" in d for d in docs)

    def test_large_text_without_doc_extensions_returns_fast_and_empty(self) -> None:
        text = "agent discussion without document suffixes " * 20_000
        assert _find_doc_paths(text) == []

    def test_excludes_urls(self) -> None:
        docs = _find_doc_paths("See https://example.com/readme.md and local-guide.md")
        assert not any("https://" in d for d in docs)
        assert not any("local-guide.md" in d for d in docs)

    def test_clean_doc_candidate_keeps_only_useful_docs(self) -> None:
        assert _clean_doc_candidate("README.md") == "README.md"
        assert _clean_doc_candidate("docs/guide.md") == "docs/guide.md"
        assert _clean_doc_candidate("C:\\Project\\docs\\guide.md") == "C:\\Project\\docs\\guide.md"
        assert _clean_doc_candidate("random.json") is None
        assert _clean_doc_candidate("(`package.json") is None

    def test_clean_doc_candidate_filters_agent_internals(self) -> None:
        assert _clean_doc_candidate("C:\\Users\\dev\\.codex\\skills\\.system\\skill\\SKILL.md") is None
        assert _clean_doc_candidate("C:\\repo\\.agent\\current_task.md") is None
        assert _clean_doc_candidate(".agent/current_task.md") is None
        assert _clean_doc_candidate("C:\\Users\\dev\\.codex\\memories\\project.md") == (
            "C:\\Users\\dev\\.codex\\memories\\project.md"
        )


# ── deduplication ─────────────────────────────────────────────────────

class TestDeduplication:
    def test_deduplicate_projects_merges_sources(self) -> None:
        candidates = [
            CandidateProject(root="/opt/example-app", discovered_from=["codex-sessions"]),
            CandidateProject(root="/opt/example-app", discovered_from=["claude-projects"]),
        ]
        result = _deduplicate_projects(candidates)
        assert len(result) == 1
        assert set(result[0].discovered_from) == {"codex-sessions", "claude-projects"}

    def test_deduplicate_projects_case_insensitive(self) -> None:
        candidates = [
            CandidateProject(root="C:\\Users\\Dev\\MyApp", discovered_from=["codex-sessions"]),
            CandidateProject(root="c:\\users\\dev\\myapp", discovered_from=["claude-projects"]),
        ]
        result = _deduplicate_projects(candidates)
        assert len(result) == 1

    def test_deduplicate_projects_preserves_unix_remote_paths(self) -> None:
        candidates = [
            CandidateProject(root="/opt/example-app", discovered_from=["codex-sessions"]),
            CandidateProject(root="/opt/example-app", discovered_from=["claude-projects"]),
        ]
        result = _deduplicate_projects(candidates)
        assert len(result) == 1
        assert result[0].root == "/opt/example-app"

    def test_deduplicate_documents(self) -> None:
        candidates = [
            CandidateDocument(path="readme.md", discovered_from=["codex-sessions"]),
            CandidateDocument(path="readme.md", discovered_from=["claude-projects"]),
            CandidateDocument(path="guide.md", discovered_from=["codex-sessions"]),
        ]
        result = _deduplicate_documents(candidates)
        assert len(result) == 2

    def test_deduplicate_servers(self) -> None:
        servers = [
            ServerAlias(alias="example-core", source="codex-sessions"),
            ServerAlias(alias="example-core", source="claude-projects"),
            ServerAlias(alias="example-engine-host", source="codex-sessions"),
        ]
        result = _deduplicate_servers(servers)
        assert len(result) == 2
        truenex = next(s for s in result if s.alias == "example-core")
        assert "codex-sessions" in truenex.source
        assert "claude-projects" in truenex.source

    def test_deduplicate_servers_uses_distinct_sources_not_substrings(self) -> None:
        servers = [
            ServerAlias(alias="example-core", source="codex"),
            ServerAlias(alias="example-core", source="codex-sessions"),
        ]
        result = _deduplicate_servers(servers)
        assert len(result) == 1
        assert result[0].source == "codex,codex-sessions"


# ── domain objects ─────────────────────────────────────────────────────

class TestDomainObjects:
    def test_discovery_report_to_dict(self) -> None:
        report = DiscoveryReport(
            agent_roots=[
                AgentRoot(label="codex-sessions", path=Path("/tmp/.codex/sessions"), exists=True, file_count=3),
            ],
            projects=[
                CandidateProject(root="/opt/example-app", discovered_from=["codex-sessions"], confidence=1.0),
            ],
            documents=[
                CandidateDocument(path="readme.md", discovered_from=["claude-projects"], confidence=1.5),
            ],
            servers=[
                ServerAlias(alias="example-core", source="codex-sessions", confidence=1.5),
            ],
            warnings=["test warning"],
        )
        d = report.to_dict()
        assert len(d["agent_roots"]) == 1
        assert d["agent_roots"][0]["label"] == "codex-sessions"
        assert len(d["projects"]) == 1
        assert d["projects"][0]["root"] == "/opt/example-app"
        assert d["projects"][0]["evidence_count"] == 1
        assert d["projects"][0]["confidence"] == 1.0
        assert len(d["documents"]) == 1
        assert d["documents"][0]["evidence_count"] == 1
        assert d["documents"][0]["confidence"] == 1.5
        assert len(d["servers"]) == 1
        assert d["servers"][0]["evidence_count"] == 1
        assert d["servers"][0]["confidence"] == 1.5
        assert len(d["warnings"]) == 1

    def test_report_counts(self) -> None:
        report = DiscoveryReport(
            projects=[CandidateProject(root="/a"), CandidateProject(root="/b")],
            documents=[CandidateDocument(path="x.md")],
            servers=[ServerAlias("s1"), ServerAlias("s2"), ServerAlias("s3")],
        )
        assert report.project_count == 2
        assert report.document_count == 1
        assert report.server_count == 3
        assert report.warning_count == 0

    def test_agent_root_defaults(self) -> None:
        root = AgentRoot(label="test", path=Path("/tmp/test"), exists=False)
        assert root.file_count == 0
        assert root.warnings == []


# ── core discovery ────────────────────────────────────────────────────

class TestDiscoverFromAgents:
    def test_discovers_agent_roots(self) -> None:
        home = _workdir("disco_roots")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        assert len(report.agent_roots) == 7
        labels = {r.label for r in report.agent_roots}
        assert labels == {
            "codex-sessions",
            "codex-history",
            "codex-memories",
            "claude-projects",
            "claude-commands",
            "claude-history",
            "claude-skills",
        }

    def test_discovers_projects_from_codex(self) -> None:
        home = _workdir("disco_proj_codex")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        project_roots = [p.root.lower() for p in report.projects]
        # Should find the cwd path from codex sessions
        assert any("truenex-memory" in r for r in project_roots)

    def test_discovers_projects_from_claude(self) -> None:
        home = _workdir("disco_proj_claude")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        project_roots = [p.root.lower() for p in report.projects]
        assert any("sample-agent" in r for r in project_roots)

    def test_discovers_ssh_servers(self) -> None:
        home = _workdir("disco_servers")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        aliases = {s.alias for s in report.servers}
        assert "example-core" in aliases
        assert "example-engine-host" in aliases
        assert "history-core" in aliases
        assert "claude-history-server" in aliases

    def test_discovers_documents(self) -> None:
        home = _workdir("disco_docs")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        doc_paths = {d.path.lower() for d in report.documents}
        assert any("readme.md" in p for p in doc_paths)
        assert any("guide.md" in p for p in doc_paths)

    def test_no_full_disk_scan__only_agent_roots(self) -> None:
        """Discovery must not scan outside agent root directories."""
        home = _workdir("disco_no_scan")
        _setup_fake_home(home)

        # Create a directory outside agent roots that should NOT be scanned
        extra_dir = home / "some-random-project"
        extra_dir.mkdir(parents=True)
        (extra_dir / "secret.txt").write_text("should not be found", encoding="utf-8")

        report = discover_from_agents(home)

        # The extra directory should not appear as a discovered document
        doc_paths = [d.path.lower() for d in report.documents]
        assert not any("secret.txt" in p for p in doc_paths)

        # The extra directory should not appear as a project by itself
        project_roots = [p.root.lower() for p in report.projects]
        assert not any("some-random-project" in p for p in project_roots)

    def test_api_routes_are_not_projects(self) -> None:
        home = _workdir("disco_api_routes")
        sessions = home / ".codex" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "session.jsonl").write_text(
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": "Call /api/v1/admin/users and then inspect /opt/example-app",
                    }],
                },
            }) + "\n",
            encoding="utf-8",
        )

        report = discover_from_agents(home)

        assert any(p.root == "/opt/example-app" for p in report.projects)
        assert not any("/api/v1/admin/users" in p.root for p in report.projects)

    def test_missing_roots_reported_as_not_found(self) -> None:
        """Roots that don't exist should be flagged."""
        home = _workdir("disco_missing")
        # _workdir already creates the directory, so no agent dirs exist within it

        report = discover_from_agents(home)

        for r in report.agent_roots:
            assert not r.exists or r.file_count == 0

    def test_empty_roots_no_errors(self) -> None:
        """Empty agent directories should not cause errors."""
        home = _workdir("disco_empty_roots")
        (home / ".codex" / "sessions").mkdir(parents=True)
        (home / ".codex" / "memories").mkdir(parents=True)
        (home / ".claude" / "projects").mkdir(parents=True)
        (home / ".claude" / "commands").mkdir(parents=True)

        report = discover_from_agents(home)

        assert report.project_count == 0
        assert report.document_count == 0
        assert report.server_count == 0

    def test_skips_nested_developer_response_items(self) -> None:
        home = _workdir("disco_no_developer")
        sessions = home / ".codex" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "session.jsonl").write_text(
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{
                        "type": "input_text",
                        "text": "Do not discover C:\\Secret\\DeveloperOnly or ssh developer-only",
                    }],
                },
            }) + "\n" +
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": "Discover C:\\Users\\dev\\Documents\\RealProject and ssh real-server",
                    }],
                },
            }) + "\n",
            encoding="utf-8",
        )

        report = discover_from_agents(home)

        assert any("RealProject" in p.root for p in report.projects)
        assert not any("DeveloperOnly" in p.root for p in report.projects)
        aliases = {s.alias for s in report.servers}
        assert "real-server" in aliases
        assert "developer-only" not in aliases


# ── report formatting ─────────────────────────────────────────────────

class TestFormatReport:
    def test_format_includes_all_sections(self) -> None:
        report = DiscoveryReport(
            agent_roots=[
                AgentRoot(label="codex-sessions", path=Path("/tmp/.codex/sessions"), exists=True, file_count=2),
            ],
            projects=[CandidateProject(root="/opt/example-app", discovered_from=["codex-sessions"], confidence=1.0)],
            documents=[CandidateDocument(path="readme.md", discovered_from=["codex-sessions"], confidence=1.5)],
            servers=[ServerAlias(alias="example-core", source="codex-sessions", confidence=1.5)],
        )
        formatted = format_report(report, limit=None)
        assert "Agent Roots" in formatted
        assert "Projects" in formatted
        assert "Documents" in formatted
        assert "Servers" in formatted
        assert "/opt/example-app" in formatted
        assert "example-core" in formatted
        assert "readme.md" in formatted
        assert "conf=" in formatted

    def test_format_shows_counts(self) -> None:
        report = DiscoveryReport(
            projects=[CandidateProject(root="/a", confidence=1.0), CandidateProject(root="/b", confidence=1.0)],
            servers=[ServerAlias("s1", confidence=1.0)],
        )
        formatted = format_report(report, limit=None)
        assert "Projects (2)" in formatted
        assert "Servers (1)" in formatted

    def test_format_shows_not_found_roots(self) -> None:
        report = DiscoveryReport(
            agent_roots=[AgentRoot(label="claude-projects", path=Path("/nonexistent"), exists=False)],
        )
        formatted = format_report(report, limit=None)
        assert "NOT FOUND" in formatted

    def test_format_warnings_section(self) -> None:
        report = DiscoveryReport(warnings=["Something went wrong"])
        formatted = format_report(report, limit=None)
        assert "Something went wrong" in formatted
        assert "Warnings/Errors (1)" in formatted

    def test_truncation_note_when_over_limit(self) -> None:
        """Markdown output shows a truncation note when candidates exceed the limit."""
        projects = [CandidateProject(root=f"/opt/proj{i}", confidence=2.0) for i in range(5)]
        report = DiscoveryReport(projects=projects)
        formatted = format_report(report, limit=3)
        assert "and 2 more" in formatted
        assert "use --json for full list" in formatted

    def test_no_truncation_note_within_limit(self) -> None:
        """No truncation note when candidates are within the limit."""
        projects = [CandidateProject(root=f"/opt/proj{i}", confidence=2.0) for i in range(3)]
        report = DiscoveryReport(projects=projects)
        formatted = format_report(report, limit=5)
        assert "... and" not in formatted

    def test_limit_none_shows_all(self) -> None:
        """Passing limit=None shows all candidates without truncation."""
        projects = [CandidateProject(root=f"/opt/proj{i}", confidence=2.0) for i in range(50)]
        report = DiscoveryReport(projects=projects)
        formatted = format_report(report, limit=None)
        # All 50 projects should be listed
        for i in range(50):
            assert f"/opt/proj{i}" in formatted
        assert "..." not in formatted

    def test_confidence_order_highest_first(self) -> None:
        """Report preserves the confidence-sorted order from DiscoveryReport."""
        # simulate what discover_from_agents produces: sorted by (-confidence, root)
        projects = sorted(
            [
                CandidateProject(root="/opt/low", discovered_from=["codex-sessions"], confidence=1.0),
                CandidateProject(root="/opt/high", discovered_from=["codex-sessions", "claude-projects"], confidence=2.0),
                CandidateProject(root="/opt/mid", discovered_from=["claude-projects"], confidence=1.0),
            ],
            key=lambda p: (-p.confidence, p.root.lower()),
        )
        report = DiscoveryReport(projects=projects)
        formatted = format_report(report, limit=None)
        lines = formatted.splitlines()
        # Find positions
        pos_high = next(i for i, l in enumerate(lines) if "/opt/high" in l)
        pos_low = next(i for i, l in enumerate(lines) if "/opt/low" in l)
        pos_mid = next(i for i, l in enumerate(lines) if "/opt/mid" in l)
        assert pos_high < pos_mid
        assert pos_low < pos_mid

    def test_servers_section_truncation(self) -> None:
        """Truncation works for server sections too."""
        servers = [ServerAlias(alias=f"srv-{i}", confidence=1.0) for i in range(10)]
        report = DiscoveryReport(servers=servers)
        formatted = format_report(report, limit=4)
        assert "and 6 more" in formatted

    def test_documents_section_truncation(self) -> None:
        """Truncation works for document sections too."""
        docs = [CandidateDocument(path=f"doc{i}.md", confidence=1.0) for i in range(8)]
        report = DiscoveryReport(documents=docs)
        formatted = format_report(report, limit=5)
        assert "and 3 more" in formatted


# ── CLI ───────────────────────────────────────────────────────────────

class TestCliGlobalDiscover:
    def test_help_shows_discover_command(self) -> None:
        result = runner.invoke(app, ["global", "discover", "--help"])
        output = plain_cli_output(result.stdout)
        assert result.exit_code == 0
        assert "--from-agents" in output
        assert "--home" in output
        assert "--json" in output
        assert "--output" in output
        assert "--limit" in output

    def test_json_output(self) -> None:
        home = _workdir("cli_json")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
            "--json",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "agent_roots" in payload
        assert "projects" in payload
        assert "documents" in payload
        assert "servers" in payload
        assert "warnings" in payload

    def test_text_output_includes_server_names(self) -> None:
        home = _workdir("cli_txt_srv")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
        ])
        assert result.exit_code == 0
        assert "example-core" in result.stdout
        assert "example-engine-host" in result.stdout

    def test_text_output_includes_project_names(self) -> None:
        home = _workdir("cli_txt_proj")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
        ])
        assert result.exit_code == 0
        # The text output should mention discovered project paths
        assert "truenex-memory" in result.stdout.lower() or "truenex_memory" in result.stdout.lower()

    def test_text_output_shows_counts(self) -> None:
        home = _workdir("cli_counts")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
        ])
        assert result.exit_code == 0
        assert "Agent Roots" in result.stdout
        assert "Projects" in result.stdout
        assert "Documents" in result.stdout
        assert "Servers" in result.stdout

    def test_write_json_output_file(self) -> None:
        home = _workdir("cli_output_json")
        _setup_fake_home(home)
        output_path = home / "report.json"

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
            "--output", str(output_path),
        ])
        assert result.exit_code == 0
        assert output_path.exists()
        assert "# Agent Discovery Report" not in result.stdout
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert "agent_roots" in data
        assert "projects" in data

    def test_write_markdown_output_file(self) -> None:
        home = _workdir("cli_output_md")
        _setup_fake_home(home)
        output_path = home / "report.md"

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
            "--output", str(output_path),
        ])
        assert result.exit_code == 0
        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "# Agent Discovery Report" in content

    def test_does_not_mutate_memory_db(self) -> None:
        home = _workdir("cli_no_mutate")
        _setup_fake_home(home)
        db_path = home / ".truenex-memory" / "truenex_memory.db"

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
        ])
        assert result.exit_code == 0
        assert not db_path.exists()

    def test_text_output_includes_not_found_for_missing_roots(self) -> None:
        home = _workdir("cli_missing_roots")
        # _workdir already creates the directory; no agent dirs within it

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
        ])
        assert result.exit_code == 0
        assert "NOT FOUND" in result.stdout


# ── confidence scoring ────────────────────────────────────────────────

class TestConfidenceScoring:
    def test_project_confidence_with_existing_dir(self) -> None:
        """Project confidence = evidence_count + 1.0 when dir exists."""
        wd = _workdir("score_proj")
        proj = CandidateProject(
            root=str(wd),
            discovered_from=["codex-sessions", "claude-projects"],
        )
        score = _score_project(proj)
        assert score == 3.0  # 2 evidence + 1 exists

    def test_project_confidence_without_existing_dir(self) -> None:
        """Project confidence = evidence_count only when dir doesn't exist."""
        proj = CandidateProject(
            root="/nonexistent/path",
            discovered_from=["codex-sessions"],
        )
        score = _score_project(proj)
        assert score == 1.0  # 1 evidence, no existence bonus

    def test_document_confidence_with_canonical_name(self) -> None:
        """Document confidence includes +0.5 for canonical doc names."""
        wd = _workdir("score_doc")
        readme = wd / "README.md"
        readme.write_text("content")
        doc = CandidateDocument(
            path=str(readme),
            discovered_from=["codex-sessions", "claude-projects"],
        )
        score = _score_document(doc)
        assert score == 3.5  # 2 evidence + 1 exists + 0.5 canonical

    def test_document_confidence_plain(self) -> None:
        """Document confidence without canonical name or existence."""
        doc = CandidateDocument(
            path="/nonexistent/notes.md",
            discovered_from=["codex-sessions"],
        )
        score = _score_document(doc)
        assert score == 1.0  # 1 evidence, no exists, no canonical

    def test_document_confidence_relative_readme_does_not_use_cwd_existence(self) -> None:
        doc = CandidateDocument(path="README.md", discovered_from=["codex-sessions"])
        score = _score_document(doc)
        assert score == 1.5  # 1 evidence + 0.5 canonical, no cwd-based exists bonus

    def test_server_confidence_fqdn(self) -> None:
        """Server confidence includes +0.5 for FQDN."""
        srv = ServerAlias(alias="db.example.com", source="codex-sessions,claude-projects")
        score = _score_server(srv)
        assert score == 2.5  # 2 evidence + 0.5 FQDN

    def test_server_confidence_hyphenated_alias(self) -> None:
        """Server confidence includes +0.5 for hyphenated aliases."""
        srv = ServerAlias(alias="example-core", source="codex-sessions,claude-projects")
        score = _score_server(srv)
        assert score == 2.5  # 2 evidence + 0.5 hyphen

    def test_server_confidence_simple_alias(self) -> None:
        """Server confidence for simple alias without FQDN."""
        srv = ServerAlias(alias="myalias", source="codex-sessions")
        score = _score_server(srv)
        assert score == 1.0  # 1 evidence, no FQDN, no hyphen


# ── ranking order ─────────────────────────────────────────────────────

class TestRanking:
    def test_projects_sorted_by_confidence_desc(self) -> None:
        home = _workdir("rank_proj")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        # Verify projects are in descending confidence order
        confidences = [p.confidence for p in report.projects]
        for i in range(len(confidences) - 1):
            assert confidences[i] >= confidences[i + 1], (
                f"Projects not sorted by confidence desc at index {i}"
            )

    def test_documents_sorted_by_confidence_desc(self) -> None:
        home = _workdir("rank_docs")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        if report.documents:
            confidences = [d.confidence for d in report.documents]
            for i in range(len(confidences) - 1):
                assert confidences[i] >= confidences[i + 1]

    def test_servers_sorted_by_confidence_desc(self) -> None:
        home = _workdir("rank_srv")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        confidences = [s.confidence for s in report.servers]
        for i in range(len(confidences) - 1):
            assert confidences[i] >= confidences[i + 1], (
                f"Servers not sorted by confidence desc at index {i}"
            )

    def test_multi_source_project_ranks_higher(self) -> None:
        """A project found by multiple agent roots should rank above single-source projects."""
        home = _workdir("rank_multi")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        if report.project_count >= 2:
            # Projects from multiple sources should have higher confidence
            multi_src = [p for p in report.projects if len(p.discovered_from) >= 2]
            single_src = [p for p in report.projects if len(p.discovered_from) == 1]
            if multi_src and single_src:
                for mp in multi_src:
                    for sp in single_src:
                        assert mp.confidence >= sp.confidence, (
                            f"Multi-source project {mp.root} should rank >= {sp.root}"
                        )


# ── JSON metadata ─────────────────────────────────────────────────────

class TestJsonMetadata:
    def test_json_includes_confidence_and_evidence_count(self) -> None:
        home = _workdir("json_meta")
        _setup_fake_home(home)
        report = discover_from_agents(home)
        d = report.to_dict()

        for proj in d["projects"]:
            assert "confidence" in proj
            assert "evidence_count" in proj
            assert isinstance(proj["confidence"], (int, float))
            assert isinstance(proj["evidence_count"], int)
            assert proj["evidence_count"] == len(proj["discovered_from"])

        for doc in d["documents"]:
            assert "confidence" in doc
            assert "evidence_count" in doc
            assert isinstance(doc["confidence"], (int, float))
            assert isinstance(doc["evidence_count"], int)
            assert doc["evidence_count"] == len(doc["discovered_from"])

        for srv in d["servers"]:
            assert "confidence" in srv
            assert "evidence_count" in srv
            assert isinstance(srv["confidence"], (int, float))
            assert isinstance(srv["evidence_count"], int)

    def test_json_server_evidence_count_from_merged_sources(self) -> None:
        """Server evidence_count counts distinct comma-separated sources."""
        srv = ServerAlias(alias="multi-src-srv", source="codex-sessions,claude-projects,codex-memories")
        srv.confidence = _score_server(srv)
        report = DiscoveryReport(servers=[srv])
        d = report.to_dict()
        assert d["servers"][0]["evidence_count"] == 3

    def test_json_full_list_by_default(self) -> None:
        """JSON output includes all candidates without --limit."""
        home = _workdir("json_full")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
            "--json",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # All candidates should be present (no silent truncation)
        assert "projects" in payload
        assert "documents" in payload
        assert "servers" in payload

    def test_json_with_limit_truncates(self) -> None:
        """JSON with --limit truncates candidate lists explicitly."""
        home = _workdir("json_limit")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
            "--json",
            "--limit", "1",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload["projects"]) <= 1
        assert len(payload["documents"]) <= 1
        assert len(payload["servers"]) <= 1

    def test_markdown_with_limit_truncates(self) -> None:
        """Markdown with --limit truncates to requested count."""
        home = _workdir("md_limit")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "discover",
            "--from-agents",
            "--home", str(home),
            "--limit", "1",
        ])
        assert result.exit_code == 0
        # Should show truncation note for sections with more than 1 item
        assert "... and" in result.stdout


# ── claude skills discovery ───────────────────────────────────────────

class TestClaudeSkillsDiscovery:
    def test_skill_files_promoted_as_documents(self) -> None:
        """SKILL.md files in .claude/skills/ are direct document candidates."""
        home = _workdir("skills_docs")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        doc_paths = [d.path.lower() for d in report.documents]
        assert any("truenex" in p and "skill.md" in p for p in doc_paths)
        assert any("truenex-engine" in p and "skill.md" in p for p in doc_paths)

    def test_skill_files_discovered_from_claude_skills_label(self) -> None:
        """SKILL.md documents are attributed to the claude-skills agent root."""
        home = _workdir("skills_label")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        skill_docs = [d for d in report.documents if "skill.md" in d.path.lower()]
        assert skill_docs
        for doc in skill_docs:
            assert "claude-skills" in doc.discovered_from

    def test_skill_files_have_higher_confidence_when_existing(self) -> None:
        """SKILL.md files that exist on disk get a confidence boost."""
        home = _workdir("skills_conf")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        skill_docs = [d for d in report.documents if "skill.md" in d.path.lower()]
        for doc in skill_docs:
            assert doc.confidence >= 2.0  # 1 evidence + 1 exists

    def test_missing_skills_dir_emits_not_found_root(self) -> None:
        """When .claude/skills/ does not exist the root is still reported as NOT FOUND."""
        home = _workdir("skills_missing")
        # Only create minimal structure — no .claude/skills/
        (home / ".codex" / "sessions").mkdir(parents=True)
        (home / ".claude" / "projects").mkdir(parents=True)
        report = discover_from_agents(home)

        labels = {r.label for r in report.agent_roots}
        assert "claude-skills" in labels
        skills_root = next(r for r in report.agent_roots if r.label == "claude-skills")
        assert not skills_root.exists

    def test_skill_content_enriches_project_discovery(self) -> None:
        """Text content of SKILL.md files contributes to project/server extraction."""
        home = _workdir("skills_enrich")
        _setup_fake_home(home)
        report = discover_from_agents(home)

        # The fake SKILL.md mentions /opt/example-app and /home/dev/example-engine
        project_roots = [p.root.lower() for p in report.projects]
        assert any("/opt/example-app" in r for r in project_roots)

    def test_collect_skill_documents_empty_when_dir_missing(self) -> None:
        from truenex_memory.discovery.agent_discovery import _collect_skill_documents
        wd = _workdir("collect_missing")
        docs = _collect_skill_documents(wd / "nonexistent", "claude-skills")
        assert docs == []

    def test_collect_skill_documents_returns_md_files(self) -> None:
        from truenex_memory.discovery.agent_discovery import _collect_skill_documents
        wd = _workdir("collect_md")
        skills = wd / "skills"
        skills.mkdir()
        (skills / "sub").mkdir()
        (skills / "sub" / "SKILL.md").write_text("# skill", encoding="utf-8")
        (skills / "sub" / "other.txt").write_text("notes", encoding="utf-8")
        (skills / "sub" / "data.bin").write_text("binary", encoding="utf-8")

        docs = _collect_skill_documents(skills, "claude-skills")
        paths = [d.path for d in docs]
        assert any("SKILL.md" in p for p in paths)
        assert any("other.txt" in p for p in paths)
        assert not any("data.bin" in p for p in paths)  # .bin not in DOC_EXTENSIONS


# ── guard: no full-disk scan assumptions ──────────────────────────────

class TestNoFullDiskScan:
    def test_discovery_only_reads_agent_roots(self, monkeypatch) -> None:
        """Verify that discovery only reads from agent root paths."""
        home = _workdir("guard_scan")
        _setup_fake_home(home)

        # Ensure the function doesn't walk the entire home directory
        # by checking that the returned roots are only agent directories
        report = discover_from_agents(home)

        agent_paths = {str(r.path) for r in report.agent_roots}
        for path_str in agent_paths:
            assert ".codex" in path_str or ".claude" in path_str, (
                f"Unexpected path scanned: {path_str}"
            )

    def test_does_not_scan_system_dirs(self) -> None:
        """Discovery should not look at /etc, /var, C:\\Windows, etc."""
        home = _workdir("guard_sysdirs")
        _setup_fake_home(home)

        report = discover_from_agents(home)

        for r in report.agent_roots:
            path_str = str(r.path).lower()
            assert "windows" not in path_str or ".claude" in path_str or ".codex" in path_str
            assert "system32" not in path_str


# ── bounded discovery (truncation safety) ─────────────────────────────

class TestBoundedDiscovery:
    """Tests that large files are bounded and don't crash discovery."""

    def test_jsonl_stops_at_max_texts(self) -> None:
        """JSONL with more entries than MAX_TEXTS_PER_JSONL_FILE stops early."""
        count = MAX_TEXTS_PER_JSONL_FILE + 200
        wd = _workdir("bounded_jsonl_max")
        jsonl_path = wd / "large.jsonl"
        lines = []
        for i in range(count):
            lines.append(json.dumps({
                "type": "session_meta",
                "payload": {"cwd": f"/opt/project-{i}"},
            }))
        jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        texts, was_truncated = _extract_text_from_jsonl(jsonl_path)
        assert len(texts) == MAX_TEXTS_PER_JSONL_FILE
        assert was_truncated is True

    def test_jsonl_small_file_not_truncated(self) -> None:
        """Small JSONL file under the limit has was_truncated=False."""
        wd = _workdir("bounded_jsonl_small")
        jsonl_path = wd / "small.jsonl"
        # Use response_item messages which produce 1 text each.
        jsonl_path.write_text(
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "project /opt/myapp"}],
                },
            }) + "\n" +
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "project /opt/other"}],
                },
            }) + "\n",
            encoding="utf-8",
        )

        texts, was_truncated = _extract_text_from_jsonl(jsonl_path)
        assert len(texts) == 2
        assert was_truncated is False

    def test_text_file_bounded_read(self) -> None:
        """A text file larger than MAX_FILE_READ_CHARS is truncated."""
        wd = _workdir("bounded_read")
        large_file = wd / "large.md"
        chunk_size = MAX_FILE_READ_CHARS + 5000
        content = "x" * chunk_size
        large_file.write_text(content, encoding="utf-8")

        text, was_truncated = _bounded_read_text(large_file)
        assert len(text) <= MAX_FILE_READ_CHARS
        assert was_truncated is True

    def test_text_file_small_not_truncated(self) -> None:
        """A small text file is read fully without truncation."""
        wd = _workdir("bounded_read_small")
        small_file = wd / "small.txt"
        small_file.write_text("hello world", encoding="utf-8")

        text, was_truncated = _bounded_read_text(small_file)
        assert text == "hello world"
        assert was_truncated is False

    def test_json_file_partial_parse_fallback(self) -> None:
        """Bounded read of JSON that cuts mid-structure falls back to text."""
        wd = _workdir("bounded_json_fallback")
        json_file = wd / "big.json"

        entries = []
        for i in range(10_000):
            entries.append(json.dumps({"key": f"/opt/proj-{i}"}))
        content = "[" + ",".join(entries) + "]"
        json_file.write_text(content, encoding="utf-8")

        raw, was_truncated = _bounded_read_text(json_file)
        assert was_truncated is True

        # json.loads should not crash on partial JSON.
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            pass  # expected fallback path

    def test_large_jsonl_still_finds_project_paths(self) -> None:
        """Even when truncated, early project paths are still discovered."""
        wd = _workdir("bounded_discover")
        codex_sessions = wd / ".codex" / "sessions"
        codex_sessions.mkdir(parents=True)

        early_lines = [
            json.dumps({
                "type": "session_meta",
                "payload": {"cwd": "D:\\Project_sw\\ProjectPy\\MyApp"},
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_text",
                                 "text": "Work on /opt/server-app"}],
                },
            }),
        ]
        filler_lines = [
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_text",
                                 "text": f"filler message {i}"}],
                },
            })
            for i in range(MAX_TEXTS_PER_JSONL_FILE + 100)
        ]
        all_lines = early_lines + filler_lines
        (codex_sessions / "large.jsonl").write_text(
            "\n".join(all_lines) + "\n", encoding="utf-8",
        )

        report = discover_from_agents(wd)
        project_roots = [p.root.lower() for p in report.projects]
        assert any("myapp" in r for r in project_roots), (
            "Should find early project path MyApp even with truncation"
        )
        assert any("server-app" in r for r in project_roots), (
            "Should find early project path server-app even with truncation"
        )

    def test_truncation_warning_in_report(self) -> None:
        """Truncation warnings appear in the DiscoveryReport warnings."""
        wd = _workdir("bounded_warning")
        codex_sessions = wd / ".codex" / "sessions"
        codex_sessions.mkdir(parents=True)

        lines = [
            json.dumps({
                "type": "session_meta",
                "payload": {"cwd": f"/opt/proj-{i}"},
            })
            for i in range(MAX_TEXTS_PER_JSONL_FILE + 50)
        ]
        (codex_sessions / "overflow.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8",
        )

        report = discover_from_agents(wd)
        truncation_warnings = [
            w for w in report.warnings
            if "truncated" in w.lower()
        ]
        assert len(truncation_warnings) > 0, (
            "Expected truncation warnings in report"
        )

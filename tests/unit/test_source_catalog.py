"""Unit tests for the source catalog domain model and CLI commands."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from contextlib import contextmanager
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from tests.unit.cli_output import plain_cli_output
from truenex_memory.cli.main import app, _filter_catalog_entries
from truenex_memory.discovery.agent_discovery import (
    AgentRoot,
    CandidateDocument,
    CandidateProject,
    DiscoveryReport,
    ServerAlias,
)
from truenex_memory.discovery.source_catalog import (
    DEFAULT_CATALOG_PATH,
    CatalogEntry,
    SourceCatalog,
    _infer_project_name_from_doc,
    candidate_to_entry,
    default_catalog_path,
    entries_to_dict,
    format_entries,
    report_to_entries,
    source_id,
)

runner = CliRunner()


# ── helpers ───────────────────────────────────────────────────────────

def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parents[1] / "unit" / f"task_work_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _entry(
    entry_id: str,
    *,
    source_type: str = "project_root",
    path_or_alias: str = "",
    project_name: str | None = None,
    discovered_from: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=entry_id,
        source_type=source_type,
        path_or_alias=path_or_alias,
        project_name=project_name,
        privacy_scope="local_private",
        discovered_from=discovered_from or [],
    )


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
    codex_sessions = home / ".codex" / "sessions"
    codex_sessions.mkdir(parents=True)
    (codex_sessions / "session1.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {
            "id": "sess-1", "cwd": "D:\\Project_sw\\ProjectPy\\truenex-memory",
            "model": "gpt-5.5",
        }}) + "\n" +
        json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Work on example-core. Connect via ssh example-core."}],
        }}) + "\n" +
        json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "The project is at /opt/example-app. See /opt/example-app/README.md for setup."}],
        }}) + "\n",
        encoding="utf-8",
    )
    (home / ".codex" / "history.jsonl").write_text(
        json.dumps({"session_id": "hist-1", "text": "Worked on /srv/history-project"}) + "\n",
        encoding="utf-8",
    )

    codex_memories = home / ".codex" / "memories"
    codex_memories.mkdir(parents=True)
    (codex_memories / "memory.json").write_text(
        json.dumps([
            {"type": "memory", "content": "Project example-engine at C:\\Users\\dev\\OneDrive\\SOFWARE\\example-engine"},
        ]) + "\n",
        encoding="utf-8",
    )

    claude_projects = home / ".claude" / "projects"
    claude_projects.mkdir(parents=True)
    (claude_projects / "project1.md").write_text(
        "# Project: sample-agent\nRoot: D:\\Project_sw\\ProjectPy\\sample-agent\nServer: ssh example-core\n",
        encoding="utf-8",
    )

    claude_commands = home / ".claude" / "commands"
    claude_commands.mkdir(parents=True)
    (claude_commands / "custom.md").write_text("# Custom\nSee config.toml\n", encoding="utf-8")
    (home / ".claude" / "history.jsonl").write_text(
        json.dumps({"text": "ssh claude-server mentioned"}) + "\n",
        encoding="utf-8",
    )


# ── stable id ─────────────────────────────────────────────────────────

class TestSourceId:
    def test_source_id_deterministic(self) -> None:
        a = source_id("project_root", "/opt/example-app")
        b = source_id("project_root", "/opt/example-app")
        assert a == b

    def test_different_type_produces_different_id(self) -> None:
        a = source_id("project_root", "/opt/example-app")
        b = source_id("server_alias", "/opt/example-app")
        assert a != b

    def test_different_path_produces_different_id(self) -> None:
        a = source_id("project_root", "/opt/example-app")
        b = source_id("project_root", "/opt/other")
        assert a != b

    def test_normalizes_backslashes(self) -> None:
        a = source_id("project_root", "C:\\Users\\dev\\MyApp")
        b = source_id("project_root", "c:/users/dev/myapp")
        assert a == b

    def test_normalizes_case(self) -> None:
        a = source_id("project_root", "/opt/EXAMPLE-APP")
        b = source_id("project_root", "/opt/example-app")
        assert a == b

    def test_normalizes_trailing_slash(self) -> None:
        a = source_id("project_root", "/opt/example-app/")
        b = source_id("project_root", "/opt/example-app")
        assert a == b

    def test_normalizes_whitespace(self) -> None:
        a = source_id("project_root", "  /opt/example-app  ")
        b = source_id("project_root", "/opt/example-app")
        assert a == b

    def test_id_starts_with_source_type_colon(self) -> None:
        sid = source_id("project_root", "/opt/example-app")
        assert sid.startswith("project_root:")

    def test_id_has_32_char_hex_suffix(self) -> None:
        sid = source_id("project_root", "/opt/example-app")
        hex_suffix = sid.split(":", 1)[1]
        assert len(hex_suffix) == 32
        assert all(c in "0123456789abcdef" for c in hex_suffix)


# ── candidate conversion ──────────────────────────────────────────────

class TestCandidateToEntry:
    def test_agent_root_to_entry(self) -> None:
        root = AgentRoot(label="codex-sessions", path=Path("/tmp/.codex/sessions"), exists=True, file_count=3)
        entry = candidate_to_entry(root)
        assert entry.source_type == "agent_root"
        assert entry.path_or_alias == str(root.path)
        assert entry.discovered_from == ["codex-sessions"]
        assert entry.confidence == 3.0
        assert entry.evidence_count == 3
        assert entry.confirmation_status == "confirmed"
        assert entry.privacy_scope == "local-private"

    def test_agent_root_to_entry_not_exists(self) -> None:
        root = AgentRoot(label="codex-sessions", path=Path("/tmp/.codex/sessions"), exists=False, file_count=0)
        entry = candidate_to_entry(root)
        assert entry.confidence == 0.0
        assert entry.evidence_count == 0

    def test_project_to_entry(self) -> None:
        proj = CandidateProject(
            root="D:\\Project_sw\\ProjectPy\\sample-agent",
            discovered_from=["codex-sessions", "claude-projects"],
            confidence=2.0,
        )
        entry = candidate_to_entry(proj)
        assert entry.source_type == "project_root"
        assert entry.path_or_alias == "D:\\Project_sw\\ProjectPy\\sample-agent"
        assert entry.project_name == "sample-agent"
        assert entry.discovered_from == ["codex-sessions", "claude-projects"]
        assert entry.confidence == 2.0
        assert entry.evidence_count == 2

    def test_project_to_entry_unix_path(self) -> None:
        proj = CandidateProject(root="/opt/example-app", discovered_from=["codex-sessions"], confidence=1.0)
        entry = candidate_to_entry(proj)
        assert entry.project_name == "example-app"

    def test_project_to_entry_stable_id(self) -> None:
        proj = CandidateProject(root="/opt/example-app", discovered_from=["codex-sessions"], confidence=1.0)
        entry = candidate_to_entry(proj)
        assert entry.id == source_id("project_root", "/opt/example-app")

    def test_document_to_entry(self) -> None:
        doc = CandidateDocument(
            path="C:\\Project\\docs\\guide.md",
            discovered_from=["codex-sessions", "claude-projects"],
            confidence=2.5,
        )
        entry = candidate_to_entry(doc)
        assert entry.source_type == "document"
        assert entry.path_or_alias == "C:\\Project\\docs\\guide.md"
        assert entry.project_name is None
        assert entry.discovered_from == ["codex-sessions", "claude-projects"]
        assert entry.confidence == 2.5
        assert entry.evidence_count == 2

    def test_document_to_entry_skill_md_infers_project_name(self) -> None:
        doc = CandidateDocument(
            path="C:\\Users\\dev\\.claude\\skills\\truenex\\SKILL.md",
            discovered_from=["claude-skills"],
            confidence=2.0,
        )
        entry = candidate_to_entry(doc)
        assert entry.source_type == "document"
        assert entry.project_name == "truenex"

    def test_document_to_entry_readme_infers_project_name(self) -> None:
        doc = CandidateDocument(
            path="/home/dev/projects/myapp/README.md",
            discovered_from=["codex-sessions"],
            confidence=1.5,
        )
        entry = candidate_to_entry(doc)
        assert entry.project_name == "myapp"

    def test_server_alias_to_entry(self) -> None:
        srv = ServerAlias(alias="example-core", source="codex-sessions,claude-projects", confidence=2.5)
        entry = candidate_to_entry(srv)
        assert entry.source_type == "server_alias"
        assert entry.path_or_alias == "example-core"
        assert entry.discovered_from == ["codex-sessions", "claude-projects"]
        assert entry.confidence == 2.5
        assert entry.evidence_count == 2

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(TypeError):
            candidate_to_entry("not-a-candidate")  # type: ignore[arg-type]


# ── _infer_project_name_from_doc ─────────────────────────────────────────

class TestInferProjectNameFromDoc:
    def test_skill_md_returns_parent(self) -> None:
        assert _infer_project_name_from_doc("projects/truenex/SKILL.md") == "truenex"

    def test_readme_md_returns_parent(self) -> None:
        assert _infer_project_name_from_doc("C:\\Users\\dev\\myproject\\README.md") == "myproject"

    def test_non_index_file_returns_none(self) -> None:
        assert _infer_project_name_from_doc("projects/truenex/guide.md") is None

    def test_bare_filename_no_parent_returns_none(self) -> None:
        assert _infer_project_name_from_doc("SKILL.md") is None

    def test_agents_md_returns_parent(self) -> None:
        assert _infer_project_name_from_doc("/opt/example-core/.claude/AGENTS.md") == ".claude"

    def test_claude_md_returns_parent(self) -> None:
        assert _infer_project_name_from_doc("D:/Projects/sample-agent/CLAUDE.md") == "sample-agent"

    def test_case_insensitive(self) -> None:
        assert _infer_project_name_from_doc("src/MyProject/ReadMe.MD") == "MyProject"

    def test_parent_is_dot_dot_returns_none(self) -> None:
        assert _infer_project_name_from_doc("../README.md") is None

    def test_parent_is_dot_returns_none(self) -> None:
        assert _infer_project_name_from_doc("./SKILL.md") is None

    def test_empty_string_returns_none(self) -> None:
        assert _infer_project_name_from_doc("") is None
        assert _infer_project_name_from_doc("   ") is None


# ── report_to_entries ─────────────────────────────────────────────────

class TestReportToEntries:
    def test_converts_all_sections(self) -> None:
        report = DiscoveryReport(
            agent_roots=[
                AgentRoot(label="codex-sessions", path=Path("/tmp/.codex/sessions"), exists=True, file_count=2),
                AgentRoot(label="claude-projects", path=Path("/tmp/.claude/projects"), exists=True, file_count=1),
            ],
            projects=[
                CandidateProject(root="/opt/proj1", discovered_from=["codex-sessions"], confidence=1.0),
                CandidateProject(root="/opt/proj2", discovered_from=["claude-projects"], confidence=1.0),
            ],
            documents=[
                CandidateDocument(path="readme.md", discovered_from=["codex-sessions"], confidence=1.0),
            ],
            servers=[
                ServerAlias(alias="srv1", source="codex-sessions", confidence=1.0),
                ServerAlias(alias="srv2", source="claude-projects", confidence=1.0),
            ],
        )
        entries = report_to_entries(report)
        # 2 agent roots + 2 projects + 1 document + 2 servers = 7
        assert len(entries) == 7
        types = {e.source_type for e in entries}
        assert types == {"agent_root", "project_root", "document", "server_alias"}

    def test_skips_nonexistent_agent_roots(self) -> None:
        report = DiscoveryReport(
            agent_roots=[
                AgentRoot(label="codex-sessions", path=Path("/tmp/.codex/sessions"), exists=True, file_count=2),
                AgentRoot(label="missing-root", path=Path("/nonexistent"), exists=False),
            ],
        )
        entries = report_to_entries(report)
        assert len(entries) == 1
        assert entries[0].source_type == "agent_root"
        assert entries[0].path_or_alias == str(Path("/tmp/.codex/sessions"))

    def test_applies_limit_per_section(self) -> None:
        """Each section is limited independently."""
        report = DiscoveryReport(
            projects=[
                CandidateProject(root=f"/opt/proj{i}", discovered_from=["codex-sessions"], confidence=float(i + 1))
                for i in range(10)
            ],
            documents=[
                CandidateDocument(path=f"doc{i}.md", discovered_from=["claude-projects"], confidence=float(i + 1))
                for i in range(8)
            ],
            servers=[
                ServerAlias(alias=f"srv-{i}", source="codex-sessions", confidence=float(i + 1))
                for i in range(6)
            ],
        )
        entries = report_to_entries(report, limit=5)
        # 5 projects + 5 documents + 5 servers = 15
        assert len(entries) == 15
        proj_count = sum(1 for e in entries if e.source_type == "project_root")
        doc_count = sum(1 for e in entries if e.source_type == "document")
        srv_count = sum(1 for e in entries if e.source_type == "server_alias")
        assert proj_count == 5
        assert doc_count == 5
        assert srv_count == 5

    def test_limit_keeps_highest_confidence(self) -> None:
        """Limiting should keep top-ranked (highest confidence) entries."""
        report = DiscoveryReport(
            projects=[
                CandidateProject(root="/opt/high", discovered_from=["codex-sessions", "claude-projects"], confidence=3.0),
                CandidateProject(root="/opt/mid", discovered_from=["codex-sessions"], confidence=1.0),
                CandidateProject(root="/opt/low", discovered_from=["claude-projects"], confidence=0.5),
            ],
        )
        entries = report_to_entries(report, limit=2)
        proj_entries = [e for e in entries if e.source_type == "project_root"]
        assert len(proj_entries) == 2
        assert proj_entries[0].path_or_alias == "/opt/high"
        assert proj_entries[1].path_or_alias == "/opt/mid"

    def test_limit_none_includes_all(self) -> None:
        report = DiscoveryReport(
            projects=[CandidateProject(root=f"/opt/proj{i}", discovered_from=["codex-sessions"], confidence=1.0) for i in range(50)],
        )
        entries = report_to_entries(report, limit=None)
        assert len(entries) == 50

    def test_report_to_entries_can_mark_candidates(self) -> None:
        report = DiscoveryReport(
            projects=[CandidateProject(root="/opt/proj", discovered_from=["codex-sessions"], confidence=1.0)],
        )
        entries = report_to_entries(report, confirmation_status="candidate")
        assert entries[0].confirmation_status == "candidate"


# ── default path ──────────────────────────────────────────────────────

class TestDefaultPath:
    def test_default_catalog_path_is_under_home(self) -> None:
        assert DEFAULT_CATALOG_PATH.name == "sources.json"
        assert ".truenex-memory" in str(DEFAULT_CATALOG_PATH)

    def test_default_catalog_path_uses_supplied_home(self) -> None:
        home = Path("C:/Users/tester")
        assert default_catalog_path(home) == home / ".truenex-memory" / "sources.json"


# ── catalog persistence ───────────────────────────────────────────────

class TestSourceCatalogSaveLoad:
    def test_save_and_load_roundtrip(self) -> None:
        wd = _workdir("catalog_save")
        catalog_path = wd / "sources.json"
        entry = CatalogEntry(
            id=source_id("project_root", "/opt/example-app"),
            source_type="project_root",
            path_or_alias="/opt/example-app",
            project_name="truenex",
            discovered_from=["codex-sessions", "claude-projects"],
            confidence=2.0,
            evidence_count=2,
        )
        sc = SourceCatalog(entries=[entry], version="1")
        sc.save(catalog_path)

        loaded = SourceCatalog.load(catalog_path)
        assert loaded.version == "1"
        assert len(loaded.entries) == 1
        assert loaded.entries[0].id == entry.id
        assert loaded.entries[0].source_type == "project_root"
        assert loaded.entries[0].path_or_alias == "/opt/example-app"
        assert loaded.entries[0].project_name == "truenex"
        assert loaded.entries[0].discovered_from == ["codex-sessions", "claude-projects"]
        assert loaded.entries[0].confidence == 2.0
        assert loaded.entries[0].evidence_count == 2

    def test_load_missing_returns_empty(self) -> None:
        sc = SourceCatalog.load(Path("/nonexistent/catalog.json"))
        assert sc.entries == []
        assert sc.version == "1"

    def test_save_creates_parent_directory(self) -> None:
        wd = _workdir("catalog_parent")
        catalog_path = wd / "subdir" / "nested" / "sources.json"
        sc = SourceCatalog(entries=[], version="1")
        sc.save(catalog_path)
        assert catalog_path.exists()
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert data["version"] == "1"
        assert data["entries"] == []

    def test_load_handles_empty_entries_key(self) -> None:
        wd = _workdir("catalog_empty")
        catalog_path = wd / "sources.json"
        catalog_path.write_text(json.dumps({"version": "3"}), encoding="utf-8")
        sc = SourceCatalog.load(catalog_path)
        assert sc.entries == []
        assert sc.version == "3"

    def test_save_is_valid_json(self) -> None:
        wd = _workdir("catalog_json")
        catalog_path = wd / "sources.json"
        sc = SourceCatalog(entries=[
            CatalogEntry(
                id="project_root:abc123",
                source_type="project_root",
                path_or_alias="/opt/example-app",
                project_name="truenex",
            )
        ])
        sc.save(catalog_path)
        raw = catalog_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert "version" in data
        assert "entries" in data


# ── entries_to_dict ───────────────────────────────────────────────────

class TestEntriesToDict:
    def test_entries_to_dict_structure(self) -> None:
        entry = CatalogEntry(
            id="project_root:abc123",
            source_type="project_root",
            path_or_alias="/opt/example-app",
            project_name="truenex",
            discovered_from=["codex-sessions"],
        )
        d = entries_to_dict([entry])
        assert d["version"] == "1"
        assert len(d["entries"]) == 1
        assert d["entries"][0]["id"] == "project_root:abc123"
        assert d["entries"][0]["source_type"] == "project_root"
        assert d["entries"][0]["path_or_alias"] == "/opt/example-app"
        assert d["entries"][0]["project_name"] == "truenex"
        assert "discovered_from" in d["entries"][0]


# ── format_entries ────────────────────────────────────────────────────

class TestFormatEntries:
    def test_format_includes_all_sections(self) -> None:
        entries = [
            CatalogEntry(id="agent_root:abc", source_type="agent_root", path_or_alias="/tmp/.codex/sessions",
                         discovered_from=["codex-sessions"], evidence_count=2, confidence=2.0),
            CatalogEntry(id="project_root:def", source_type="project_root", path_or_alias="/opt/example-app",
                         project_name="truenex", discovered_from=["codex-sessions"], confidence=1.0),
            CatalogEntry(id="document:ghi", source_type="document", path_or_alias="readme.md",
                         discovered_from=["claude-projects"], confidence=1.5),
            CatalogEntry(id="server_alias:jkl", source_type="server_alias", path_or_alias="example-core",
                         discovered_from=["codex-sessions"], confidence=2.0),
        ]
        text = format_entries(entries)
        assert "agent_root" in text
        assert "project_root" in text
        assert "document" in text
        assert "server_alias" in text
        assert "example-core" in text
        assert "truenex" in text
        assert "readme.md" in text

    def test_format_shows_counts_per_section(self) -> None:
        entries = [
            CatalogEntry(id="project_root:a", source_type="project_root", path_or_alias="/a"),
            CatalogEntry(id="project_root:b", source_type="project_root", path_or_alias="/b"),
        ]
        text = format_entries(entries)
        assert "project_root (2)" in text

    def test_format_empty_section_shows_none(self) -> None:
        entries: list[CatalogEntry] = []
        text = format_entries(entries)
        assert "(none)" in text

    def test_format_shows_summary(self) -> None:
        entries = [
            CatalogEntry(id="project_root:a", source_type="project_root", path_or_alias="/a"),
        ]
        text = format_entries(entries)
        assert "Summary" in text
        assert "Total entries: 1" in text

    def test_format_review_tag(self) -> None:
        text = format_entries([])
        assert "review only" in text


# ── CLI catalog filtering helper ──────────────────────────────────────

class TestFilterCatalogEntries:
    def test_include_is_case_insensitive_any_match(self) -> None:
        entries = [
            _entry("a", path_or_alias="D:\\Projects\\sample-agent"),
            _entry("b", path_or_alias="/opt/example-core"),
        ]

        filtered = _filter_catalog_entries(
            entries,
            include=["sample-agent", "does-not-exist"],
            exclude=None,
            source_type=None,
        )

        assert filtered == [entries[0]]

    def test_exclude_wins_over_include(self) -> None:
        entries = [
            _entry("a", path_or_alias="/opt/example-core"),
            _entry("b", path_or_alias="/opt/example-app-engine"),
        ]

        filtered = _filter_catalog_entries(
            entries,
            include=["example-app"],
            exclude=["core"],
            source_type=None,
        )

        assert filtered == [entries[1]]

    def test_combines_include_exclude_and_source_type(self) -> None:
        entries = [
            _entry("a", source_type="project_root", path_or_alias="/opt/example-core"),
            _entry("b", source_type="server_alias", path_or_alias="example-core"),
            _entry("c", source_type="server_alias", path_or_alias="example-dev"),
        ]

        filtered = _filter_catalog_entries(
            entries,
            include=["example-core"],
            exclude=["dev"],
            source_type=["server_alias"],
        )

        assert filtered == [entries[1]]

    def test_ignores_blank_filters_and_missing_attributes(self) -> None:
        entries = [SimpleNamespace(id="example-core")]

        filtered = _filter_catalog_entries(
            entries,
            include=[" ", "EXAMPLE"],
            exclude=[" "],
            source_type=None,
        )

        assert filtered == entries


# ── CLI: sources review ───────────────────────────────────────────────

class TestCliSourcesReview:
    def test_help_shows_review_command(self) -> None:
        result = runner.invoke(app, ["global", "sources", "review", "--help"])
        output = plain_cli_output(result.stdout)
        assert result.exit_code == 0
        assert "--home" in output
        assert "--json" in output
        assert "--limit" in output
        assert "--include" in output
        assert "--exclude" in output
        assert "--source-type" in output

    def test_review_text_output(self) -> None:
        home = _workdir("cli_review_text")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "sources", "review",
            "--home", str(home),
        ])
        assert result.exit_code == 0
        assert "project_root" in result.stdout
        assert "server_alias" in result.stdout
        assert "document" in result.stdout

    def test_review_json_output(self) -> None:
        home = _workdir("cli_review_json")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "sources", "review",
            "--home", str(home),
            "--json",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "version" in payload
        assert "entries" in payload
        assert isinstance(payload["entries"], list)
        assert all(entry["confirmation_status"] == "candidate" for entry in payload["entries"])

    def test_review_does_not_mutate_db_or_catalog(self) -> None:
        home = _workdir("cli_review_no_mutate")
        _setup_fake_home(home)
        catalog_path = home / ".truenex-memory" / "sources.json"
        db_path = home / ".truenex-memory" / "truenex_memory.db"

        result = runner.invoke(app, [
            "global", "sources", "review",
            "--home", str(home),
            "--json",
        ])
        assert result.exit_code == 0
        assert not catalog_path.exists()
        assert not db_path.exists()

    def test_review_respects_limit(self) -> None:
        home = _workdir("cli_review_limit")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "sources", "review",
            "--home", str(home),
            "--json",
            "--limit", "2",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # Non-agent sections must respect the per-section limit of 2.
        # Agent roots are not limited (they are always included).
        section_counts: dict[str, int] = {}
        for entry in payload["entries"]:
            section_counts[entry["source_type"]] = section_counts.get(entry["source_type"], 0) + 1
        for source_type, count in section_counts.items():
            if source_type != "agent_root":
                assert count <= 2, f"{source_type} has {count} entries, expected <= 2"

    def test_review_filters_by_include_and_source_type(self) -> None:
        home = _workdir("cli_review_filter")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "sources", "review",
            "--home", str(home),
            "--json",
            "--include", "sample-agent",
            "--source-type", "project_root",
        ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["entries"]
        assert all(entry["source_type"] == "project_root" for entry in payload["entries"])
        assert all("sample-agent" in entry["path_or_alias"].lower() for entry in payload["entries"])

    def test_review_excludes_entries(self) -> None:
        home = _workdir("cli_review_exclude")
        _setup_fake_home(home)

        result = runner.invoke(app, [
            "global", "sources", "review",
            "--home", str(home),
            "--json",
            "--source-type", "server_alias",
            "--exclude", "example-core",
        ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert all("example-core" not in entry["path_or_alias"].lower() for entry in payload["entries"])


# ── CLI: sources confirm ──────────────────────────────────────────────

class TestCliSourcesConfirm:
    def test_help_shows_confirm_command(self) -> None:
        result = runner.invoke(app, ["global", "sources", "confirm", "--help"])
        output = plain_cli_output(result.stdout)
        assert result.exit_code == 0
        assert "--home" in output
        assert "--catalog" in output
        assert "--limit" in output
        assert "--yes" in output
        assert "--json" in output
        assert "--include" in output
        assert "--exclude" in output
        assert "--source-type" in output

    def test_confirm_writes_catalog(self) -> None:
        home = _workdir("cli_confirm_write")
        _setup_fake_home(home)
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--yes",
        ])
        assert result.exit_code == 0
        assert "Catalog written" in result.stdout
        assert catalog_path.exists()
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert data["version"] == "1"
        assert len(data["entries"]) > 0
        assert all(entry["confirmation_status"] == "confirmed" for entry in data["entries"])

    def test_confirm_default_catalog_path_uses_home(self) -> None:
        home = _workdir("cli_confirm_default_path")
        _setup_fake_home(home)
        catalog_path = home / ".truenex-memory" / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--yes",
        ])
        assert result.exit_code == 0
        assert catalog_path.exists()
        assert str(catalog_path) in result.stdout

    def test_confirm_written_entries_have_required_fields(self) -> None:
        home = _workdir("cli_confirm_fields")
        _setup_fake_home(home)
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--yes",
        ])
        assert result.exit_code == 0
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        for entry in data["entries"]:
            assert "id" in entry
            assert "source_type" in entry
            assert "path_or_alias" in entry
            assert "discovered_from" in entry
            assert "confirmation_status" in entry
            assert "privacy_scope" in entry
            assert "confidence" in entry
            assert "evidence_count" in entry

    def test_confirm_respects_limit(self) -> None:
        home = _workdir("cli_confirm_limit")
        _setup_fake_home(home)
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--limit", "3",
            "--yes",
        ])
        assert result.exit_code == 0
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        # Each non-agent section limited to 3
        proj_count = sum(1 for e in data["entries"] if e["source_type"] == "project_root")
        doc_count = sum(1 for e in data["entries"] if e["source_type"] == "document")
        srv_count = sum(1 for e in data["entries"] if e["source_type"] == "server_alias")
        assert proj_count <= 3
        assert doc_count <= 3
        assert srv_count <= 3

    def test_confirm_filters_entries_before_writing(self) -> None:
        home = _workdir("cli_confirm_filter")
        _setup_fake_home(home)
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--include", "example-core",
            "--source-type", "server_alias",
            "--yes",
        ])

        assert result.exit_code == 0
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert data["entries"]
        assert all(entry["source_type"] == "server_alias" for entry in data["entries"])
        assert all("example-core" in entry["path_or_alias"].lower() for entry in data["entries"])

    def test_confirm_repeated_include_uses_any_match(self) -> None:
        home = _workdir("cli_confirm_include_any")
        _setup_fake_home(home)
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--include", "example-core",
            "--include", "does-not-exist",
            "--source-type", "server_alias",
            "--yes",
        ])

        assert result.exit_code == 0
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        aliases = {entry["path_or_alias"] for entry in data["entries"]}
        assert aliases == {"example-core"}

    def test_confirm_excludes_entries_before_writing(self) -> None:
        home = _workdir("cli_confirm_exclude")
        _setup_fake_home(home)
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "server_alias",
            "--exclude", "example-core",
            "--yes",
        ])

        assert result.exit_code == 0
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert all("example-core" not in entry["path_or_alias"].lower() for entry in data["entries"])

    def test_confirm_creates_parent_directory(self) -> None:
        home = _workdir("cli_confirm_mkdir")
        _setup_fake_home(home)
        catalog_path = home / "subdir" / "nested" / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--yes",
        ])
        assert result.exit_code == 0
        assert catalog_path.exists()

    def test_confirm_json_output(self) -> None:
        home = _workdir("cli_confirm_json")
        _setup_fake_home(home)
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--yes",
            "--json",
        ])
        assert result.exit_code == 0
        # JSON output is multi-line; the last line is the "Catalog written" message.
        # Parse all lines except the summary line as JSON.
        lines = result.stdout.strip().split("\n")
        json_lines = lines[:-1]  # drop "Catalog written: ..." line
        json_text = "\n".join(json_lines)
        payload = json.loads(json_text)
        assert "entries" in payload

    def test_confirm_without_yes_prompts(self) -> None:
        home = _workdir("cli_confirm_prompt")
        _setup_fake_home(home)
        catalog_path = home / "sources.json"

        # Simulate answering "y" to the prompt
        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
        ], input="y\n")
        assert result.exit_code == 0
        assert catalog_path.exists()

    def test_confirm_without_yes_aborts_on_no(self) -> None:
        home = _workdir("cli_confirm_abort")
        _setup_fake_home(home)
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "confirm",
            "--home", str(home),
            "--catalog", str(catalog_path),
        ], input="n\n")
        assert result.exit_code == 1
        assert not catalog_path.exists()
        assert "Aborted" in result.stdout


# ── SourceCatalog.upsert_entry ──────────────────────────────────────────

class TestSourceCatalogUpsertEntry:
    def test_upsert_entry_adds_to_empty_catalog(self) -> None:
        sc = SourceCatalog()
        entry = CatalogEntry(
            id=source_id("project_root", "/opt/example-app"),
            source_type="project_root",
            path_or_alias="/opt/example-app",
            project_name="truenex",
        )
        action, returned = sc.upsert_entry(entry)
        assert action == "added"
        assert returned is entry
        assert len(sc.entries) == 1
        assert sc.entries[0].id == entry.id

    def test_upsert_entry_adds_to_existing_catalog(self) -> None:
        existing = CatalogEntry(
            id=source_id("project_root", "/opt/existing"),
            source_type="project_root",
            path_or_alias="/opt/existing",
        )
        sc = SourceCatalog(entries=[existing])
        new_entry = CatalogEntry(
            id=source_id("project_root", "/opt/example-app"),
            source_type="project_root",
            path_or_alias="/opt/example-app",
            project_name="truenex",
        )
        action, _ = sc.upsert_entry(new_entry)
        assert action == "added"
        assert len(sc.entries) == 2
        ids = {e.id for e in sc.entries}
        assert existing.id in ids
        assert new_entry.id in ids

    def test_upsert_entry_updates_by_id(self) -> None:
        old_entry = CatalogEntry(
            id=source_id("project_root", "/opt/example-app"),
            source_type="project_root",
            path_or_alias="/opt/example-app",
            confidence=0.5,
            evidence_count=1,
            discovered_from=["codex-sessions"],
        )
        sc = SourceCatalog(entries=[old_entry])
        updated = CatalogEntry(
            id=source_id("project_root", "/opt/example-app"),
            source_type="project_root",
            path_or_alias="/opt/example-app",
            project_name="truenex",
            confidence=1.0,
            evidence_count=3,
            discovered_from=["codex-sessions", "claude-projects"],
        )
        action, returned = sc.upsert_entry(updated)
        assert action == "updated"
        assert returned is updated
        assert len(sc.entries) == 1
        assert sc.entries[0].confidence == 1.0
        assert sc.entries[0].evidence_count == 3
        assert sc.entries[0].project_name == "truenex"
        assert sc.entries[0].discovered_from == ["codex-sessions", "claude-projects"]

    def test_upsert_entry_preserves_other_entries(self) -> None:
        entry_a = CatalogEntry(
            id=source_id("project_root", "/opt/proj-a"),
            source_type="project_root",
            path_or_alias="/opt/proj-a",
        )
        entry_b = CatalogEntry(
            id=source_id("server_alias", "srv-b"),
            source_type="server_alias",
            path_or_alias="srv-b",
        )
        sc = SourceCatalog(entries=[entry_a, entry_b])
        updated_a = CatalogEntry(
            id=source_id("project_root", "/opt/proj-a"),
            source_type="project_root",
            path_or_alias="/opt/proj-a",
            project_name="proj-a-renamed",
        )
        action, _ = sc.upsert_entry(updated_a)
        assert action == "updated"
        assert len(sc.entries) == 2
        assert sc.entries[0].project_name == "proj-a-renamed"
        assert sc.entries[1].id == entry_b.id
        assert sc.entries[1].path_or_alias == "srv-b"


# ── CLI: sources add ────────────────────────────────────────────────────

class TestCliSourcesAdd:
    def test_help_shows_add_command(self) -> None:
        result = runner.invoke(app, ["global", "sources", "add", "--help"])
        output = plain_cli_output(result.stdout)
        assert result.exit_code == 0
        assert "--home" in output
        assert "--catalog" in output
        assert "--source-type" in output
        assert "--path-or-alias" in output
        assert "--project-name" in output
        assert "--discovered-from" in output
        assert "--confidence" in output
        assert "--evidence-count" in output
        assert "--yes" in output
        assert "--json" in output

    def test_add_creates_catalog_file(self) -> None:
        home = _workdir("cli_add_create")
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "project_root",
            "--path-or-alias", "/opt/example-app",
            "--project-name", "truenex",
            "--yes",
        ])
        assert result.exit_code == 0
        assert "Added" in result.stdout
        assert str(catalog_path) in result.stdout
        assert catalog_path.exists()
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert len(data["entries"]) == 1
        entry = data["entries"][0]
        assert entry["source_type"] == "project_root"
        assert entry["path_or_alias"] == "/opt/example-app"
        assert entry["project_name"] == "truenex"
        assert entry["confirmation_status"] == "confirmed"
        assert entry["privacy_scope"] == "local-private"

    def test_add_upserts_existing_entry(self) -> None:
        home = _workdir("cli_add_upsert")
        catalog_path = home / "sources.json"
        sid = source_id("project_root", "/opt/example-app")
        existing = {
            "version": "1",
            "entries": [{
                "id": sid,
                "source_type": "project_root",
                "path_or_alias": "/opt/example-app",
                "project_name": None,
                "discovered_from": ["codex-sessions"],
                "confirmation_status": "confirmed",
                "privacy_scope": "local-private",
                "confidence": 0.5,
                "evidence_count": 1,
            }],
        }
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(existing), encoding="utf-8")

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "project_root",
            "--path-or-alias", "/opt/example-app",
            "--project-name", "truenex-v2",
            "--confidence", "1.0",
            "--evidence-count", "2",
            "--discovered-from", "codex-sessions",
            "--discovered-from", "claude-projects",
            "--yes",
        ])
        assert result.exit_code == 0
        assert "Updated" in result.stdout
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert len(data["entries"]) == 1
        entry = data["entries"][0]
        assert entry["project_name"] == "truenex-v2"
        assert entry["confidence"] == 1.0
        assert entry["evidence_count"] == 2
        assert entry["discovered_from"] == ["codex-sessions", "claude-projects"]

    def test_add_preserves_other_entries(self) -> None:
        home = _workdir("cli_add_preserve")
        catalog_path = home / "sources.json"
        sid_a = source_id("project_root", "/opt/proj-a")
        sid_b = source_id("server_alias", "srv-b")
        existing = {
            "version": "1",
            "entries": [
                {
                    "id": sid_a,
                    "source_type": "project_root",
                    "path_or_alias": "/opt/proj-a",
                    "project_name": "proj-a",
                    "discovered_from": [],
                    "confirmation_status": "confirmed",
                    "privacy_scope": "local-private",
                    "confidence": 0.0,
                    "evidence_count": 0,
                },
                {
                    "id": sid_b,
                    "source_type": "server_alias",
                    "path_or_alias": "srv-b",
                    "project_name": None,
                    "discovered_from": ["codex-sessions"],
                    "confirmation_status": "confirmed",
                    "privacy_scope": "local-private",
                    "confidence": 1.0,
                    "evidence_count": 2,
                },
            ],
        }
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(existing), encoding="utf-8")

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "document",
            "--path-or-alias", "/docs/readme.md",
            "--yes",
        ])
        assert result.exit_code == 0
        assert "Added" in result.stdout
        assert "total: 3 entries" in result.stdout
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert len(data["entries"]) == 3
        ids = {e["id"] for e in data["entries"]}
        assert sid_a in ids
        assert sid_b in ids

    def test_add_json_output(self) -> None:
        home = _workdir("cli_add_json")
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "server_alias",
            "--path-or-alias", "truenex-prod",
            "--discovered-from", "claude-projects",
            "--confidence", "2.0",
            "--evidence-count", "3",
            "--json",
            "--yes",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["action"] == "added"
        assert payload["total_entries"] == 1
        assert "catalog_path" in payload
        assert str(catalog_path) in payload["catalog_path"]
        e = payload["entry"]
        assert e["source_type"] == "server_alias"
        assert e["path_or_alias"] == "truenex-prod"
        assert e["confirmation_status"] == "confirmed"
        assert e["privacy_scope"] == "local-private"
        assert e["confidence"] == 2.0
        assert e["evidence_count"] == 3
        assert "id" in e
        saved = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert saved["entries"][0]["path_or_alias"] == "truenex-prod"

    def test_add_json_output_without_yes_keeps_stdout_parseable(self) -> None:
        home = _workdir("cli_add_json_prompt")
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "document",
            "--path-or-alias", "/docs/status.md",
            "--json",
        ], input="y\n")

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["action"] == "added"
        assert payload["entry"]["path_or_alias"] == "/docs/status.md"
        assert "Add document:/docs/status.md" in result.stderr
        assert catalog_path.exists()

    def test_add_without_yes_prompts_and_accepts(self) -> None:
        home = _workdir("cli_add_prompt_yes")
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "project_root",
            "--path-or-alias", "/opt/example-app",
            "--project-name", "truenex",
        ], input="y\n")
        assert result.exit_code == 0
        assert catalog_path.exists()
        assert "Added" in result.stdout

    def test_add_without_yes_aborts_on_no(self) -> None:
        home = _workdir("cli_add_abort")
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "project_root",
            "--path-or-alias", "/opt/example-app",
        ], input="n\n")
        assert result.exit_code == 1
        assert not catalog_path.exists()
        assert "Aborted" in result.stdout

    def test_add_does_not_touch_sqlite_db(self) -> None:
        home = _workdir("cli_add_no_db")
        catalog_path = home / "sources.json"
        db_path = home / ".truenex-memory" / "truenex_memory.db"

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "project_root",
            "--path-or-alias", "/opt/example-app",
            "--yes",
        ])
        assert result.exit_code == 0
        assert not db_path.exists()

    def test_add_rejects_invalid_source_type(self) -> None:
        home = _workdir("cli_add_invalid_type")
        catalog_path = home / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "invalid_type",
            "--path-or-alias", "/opt/example-app",
            "--yes",
        ])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    def test_add_default_catalog_path_uses_home(self) -> None:
        home = _workdir("cli_add_default_path")
        expected_path = home / ".truenex-memory" / "sources.json"

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--source-type", "agent_root",
            "--path-or-alias", str(home / ".codex" / "sessions"),
            "--yes",
        ])
        assert result.exit_code == 0
        assert expected_path.exists()
        assert str(expected_path) in result.stdout

    def test_add_text_output_for_update(self) -> None:
        home = _workdir("cli_add_update_text")
        catalog_path = home / "sources.json"
        sid = source_id("project_root", "/opt/example-app")
        existing = {
            "version": "1",
            "entries": [{
                "id": sid,
                "source_type": "project_root",
                "path_or_alias": "/opt/example-app",
                "project_name": None,
                "discovered_from": [],
                "confirmation_status": "confirmed",
                "privacy_scope": "local-private",
                "confidence": 0.0,
                "evidence_count": 0,
            }],
        }
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(existing), encoding="utf-8")

        result = runner.invoke(app, [
            "global", "sources", "add",
            "--home", str(home),
            "--catalog", str(catalog_path),
            "--source-type", "project_root",
            "--path-or-alias", "/opt/example-app",
            "--project-name", "truenex",
            "--yes",
        ])
        assert result.exit_code == 0
        assert "Updated" in result.stdout
        assert "total: 1 entries" in result.stdout

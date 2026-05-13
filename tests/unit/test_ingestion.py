"""Unit tests for the ingestion framework."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.unit.cli_output import plain_cli_output
from truenex_memory.cli.main import app
from truenex_memory.core.config import resolve_project_config
from truenex_memory.ingestion.manifest import (
    SourceEntry,
    SourceManifest,
    IngestionRecord,
    MANIFEST_VERSION,
    VALID_SOURCE_TYPES,
    VALID_PRIVACY_SCOPES,
    PARSE_LATER_SOURCE_TYPES,
    INDEXABLE_SOURCE_TYPES,
)
from truenex_memory.ingestion.parsers import get_parser, parsers
from truenex_memory.ingestion.parsers.jsonl_sessions import (
    _build_exchanges,
    _extract_compactions,
    _extract_created_at,
    _resolve_role,
    _extract_text,
    _find_model,
    _extract_session_id,
)
from truenex_memory.ingestion.parsers.text_docs import parse_project_docs
from truenex_memory.ingestion.engine import ingest_manifest
from truenex_memory.store.repository import MemoryRepository

runner = CliRunner()


# ── helpers ──────────────────────────────────────────────────────────

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


def _write_manifest(workdir: Path, sources: list[dict[str, object]], project: str = "test-project") -> Path:
    path = workdir / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "manifest_version": MANIFEST_VERSION,
                "project": project,
                "sources": sources,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _write_text_file(workdir: Path, rel_path: str, content: str) -> Path:
    path = workdir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── SourceManifest domain model ──────────────────────────────────────

class TestSourceEntry:
    def test_valid_entry(self) -> None:
        entry = SourceEntry(source_type="project_docs", source_path="docs/")
        assert entry.source_type == "project_docs"
        assert entry.privacy_scope == "local_private"

    def test_invalid_source_type_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid source_type"):
            SourceEntry(source_type="bad_type", source_path="x")

    def test_invalid_privacy_scope_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid privacy_scope"):
            SourceEntry(source_type="project_docs", source_path="x", privacy_scope="public")

    def test_from_dict_required_fields(self) -> None:
        entry = SourceEntry.from_dict({"source_type": "agent_session", "source_path": "logs/"})
        assert entry.source_type == "agent_session"
        assert entry.source_path == "logs/"

    def test_from_dict_with_optionals(self) -> None:
        entry = SourceEntry.from_dict({
            "source_type": "project_docs",
            "source_path": "docs/",
            "source_tool": "markdown",
            "privacy_scope": "project_shared",
            "description": "Project docs",
        })
        assert entry.source_tool == "markdown"
        assert entry.privacy_scope == "project_shared"
        assert entry.description == "Project docs"

    def test_from_dict_missing_source_type_raises(self) -> None:
        with pytest.raises(ValueError, match="source_type"):
            SourceEntry.from_dict({"source_path": "x"})

    def test_from_dict_missing_source_path_raises(self) -> None:
        with pytest.raises(ValueError, match="source_path"):
            SourceEntry.from_dict({"source_type": "project_docs"})

    def test_source_types_registry(self) -> None:
        assert "project_docs" in INDEXABLE_SOURCE_TYPES
        assert "agent_session" in INDEXABLE_SOURCE_TYPES
        assert "agent_memory" in PARSE_LATER_SOURCE_TYPES
        assert "operations_note" in PARSE_LATER_SOURCE_TYPES
        assert "binary_document" in PARSE_LATER_SOURCE_TYPES
        assert VALID_PRIVACY_SCOPES == {"local_private", "project_shared"}


class TestSourceManifest:
    def test_load_valid_manifest(self) -> None:
        wd = _workdir("manifest_valid")
        path = _write_manifest(wd, [
            {"source_type": "project_docs", "source_path": "docs/"},
            {"source_type": "agent_session", "source_path": "sessions/", "source_tool": "claude-code"},
        ])
        manifest = SourceManifest.from_path(path)
        assert manifest.manifest_version == MANIFEST_VERSION
        assert manifest.project == "test-project"
        assert len(manifest.sources) == 2
        assert manifest.sources[0].source_type == "project_docs"
        assert manifest.sources[1].source_tool == "claude-code"

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="manifest not found"):
            SourceManifest.from_path(Path("/nonexistent/manifest.json"))

    def test_invalid_json_raises(self) -> None:
        wd = _workdir("manifest_badjson")
        path = wd / "bad.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            SourceManifest.from_path(path)

    def test_wrong_version_raises(self) -> None:
        wd = _workdir("manifest_badver")
        path = _write_manifest(wd, [{"source_type": "project_docs", "source_path": "docs/"}])
        # Overwrite with wrong version
        path.write_text(json.dumps({"manifest_version": "99", "project": "x", "sources": []}))
        with pytest.raises(ValueError, match="unsupported manifest_version"):
            SourceManifest.from_path(path)

    def test_missing_project_raises(self) -> None:
        wd = _workdir("manifest_noproj")
        path = wd / "manifest.json"
        path.write_text(json.dumps({"manifest_version": "1", "sources": []}))
        with pytest.raises(ValueError, match="project"):
            SourceManifest.from_path(path)

    def test_empty_sources_raises(self) -> None:
        wd = _workdir("manifest_empty")
        path = _write_manifest(wd, [])
        with pytest.raises(ValueError, match="sources"):
            SourceManifest.from_path(path)

    def test_not_a_dict_raises(self) -> None:
        wd = _workdir("manifest_notdict")
        path = wd / "manifest.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match="JSON object"):
            SourceManifest.from_path(path)

    def test_bad_source_entry_raises(self) -> None:
        wd = _workdir("manifest_badsrc")
        path = _write_manifest(wd, [{"source_type": "bad_type", "source_path": "x"}])
        with pytest.raises(ValueError, match="invalid source_type"):
            SourceManifest.from_path(path)


# ── IngestionRecord ──────────────────────────────────────────────────

class TestIngestionRecord:
    def test_basic_record(self) -> None:
        record = IngestionRecord(
            project="p", source_type="project_docs",
            source_path="docs/a.md", source_tool="markdown",
            text="hello world",
        )
        assert record.filename == "a.md"
        assert record.session_id is None
        assert record.privacy_scope == "local_private"

    def test_record_with_session(self) -> None:
        record = IngestionRecord(
            project="p", source_type="agent_session",
            source_path="logs/s.jsonl", source_tool="claude-code",
            text="digest", session_id="sess-1",
            created_at="2025-01-01T00:00:00Z",
        )
        assert record.session_id == "sess-1"
        assert record.created_at == "2025-01-01T00:00:00Z"


# ── Parser: text_docs ────────────────────────────────────────────────

class TestTextDocsParser:
    def test_parse_single_file(self) -> None:
        wd = _workdir("td_single")
        _write_text_file(wd, "readme.md", "# Hello\n\nWorld content.")
        records = parse_project_docs(wd, "proj", "markdown", "local_private")
        assert len(records) == 1
        r = records[0]
        assert r.source_type == "project_docs"
        assert r.source_path.endswith("readme.md")
        assert Path(r.source_path).is_absolute()
        assert "# Hello" in r.text
        assert r.source_tool == "markdown"
        assert r.created_at is not None

    def test_parse_directory_tree(self) -> None:
        wd = _workdir("td_tree")
        _write_text_file(wd, "docs/index.md", "Index")
        _write_text_file(wd, "docs/guide.md", "Guide")
        _write_text_file(wd, "src/main.py", "print('hi')")
        records = parse_project_docs(wd, "proj", "markdown", "local_private")
        assert len(records) == 3
        paths = {r.source_path for r in records}
        assert any(p.endswith("docs\\index.md") or p.endswith("docs/index.md") for p in paths)

    def test_excludes_hidden_dirs(self) -> None:
        wd = _workdir("td_exclude")
        _write_text_file(wd, "readme.md", "Hello")
        _write_text_file(wd, ".git/config", "config")
        _write_text_file(wd, "node_modules/pkg/index.js", "js")
        records = parse_project_docs(wd, "proj", "", "local_private")
        paths = {r.source_path for r in records}
        assert len(records) == 1
        # Only readme.md, not files in excluded dirs
        assert any("readme.md" in p for p in paths)

    def test_excludes_agent_and_test_work_dirs(self) -> None:
        wd = _workdir("td_exclude_agent_work")
        _write_text_file(wd, "readme.md", "Hello")
        _write_text_file(wd, ".agent/debug.md", "debug")
        _write_text_file(wd, "tests/unit/task_work_tmp/generated.md", "generated")
        _write_text_file(wd, "tests/unit/.task_work/generated.md", "generated")
        _write_text_file(wd, "tests/unit/.task3_work/generated.md", "generated")
        _write_text_file(wd, "tests/pytest_tmp/generated.md", "generated")
        records = parse_project_docs(wd, "proj", "", "local_private")
        paths = {Path(r.source_path).name for r in records}
        assert paths == {"readme.md"}

    def test_skips_unsupported_extensions(self) -> None:
        wd = _workdir("td_ext")
        _write_text_file(wd, "image.png", "fake png")
        _write_text_file(wd, "readme.md", "Hello")
        records = parse_project_docs(wd, "proj", "", "local_private")
        assert len(records) == 1

    def test_skips_empty_files(self) -> None:
        wd = _workdir("td_empty")
        _write_text_file(wd, "empty.md", "")
        _write_text_file(wd, "ok.md", "content")
        records = parse_project_docs(wd, "proj", "", "local_private")
        assert len(records) == 1

    def test_nonexistent_dir_returns_empty(self) -> None:
        records = parse_project_docs(Path("/nonexistent"), "proj", "", "local_private")
        assert records == []


# ── Parser: jsonl_sessions ───────────────────────────────────────────

SYNTHETIC_CLAUDE_JSONL = """\
{"type":"system","message":{"role":"system","content":"You are a coding agent."}}
{"type":"user","message":{"role":"user","content":"Write a hello world function"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Here is a hello world function in Python:"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"write","input":{"path":"hello.py"}}]}}
{"type":"user","message":{"role":"user","content":"Add a docstring"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I've added a docstring."}]}}
{"type":"compaction","message":{"content":"The user asked for hello world and a docstring. Assistant provided both."}}
"""

SYNTHETIC_CODEX_JSONL = """\
{"type":"user","message":{"content":"Fix the bug in utils.py"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Found the bug, it was a null check."}]}}
{"type":"assistant","message":{"content":[{"type":"tool_call","name":"edit","input":{}}]}}
{"type":"user","message":{"content":"Run tests"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Tests pass now."}]}}
"""

REALISTIC_CODEX_JSONL = """\
{"timestamp":"2026-05-01T22:53:01.148Z","type":"session_meta","payload":{"id":"sess-real","timestamp":"2026-05-01T22:52:54.525Z","cwd":"D:\\\\Project_sw\\\\ProjectPy\\\\truenex-memory","model":"gpt-5.5"}}
{"timestamp":"2026-05-01T22:53:02.000Z","type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"Do not index developer instructions"}]}}
{"timestamp":"2026-05-01T22:53:03.000Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Continue Truenex Memory and build ingestion."}]}}
{"timestamp":"2026-05-01T22:53:04.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Implemented ingestion manifest support."},{"type":"tool_call","name":"shell","input":{}}]}}
{"timestamp":"2026-05-01T22:53:05.000Z","type":"compacted","payload":{"summary":"Ingestion task summary with manifest and JSONL parser."}}
"""


class TestJsonlSessionsParser:
    def test_parser_registered(self) -> None:
        parser = get_parser("agent_session")
        assert parser is not None

    def test_resolve_role_codex_style(self) -> None:
        assert _resolve_role({"type": "user", "message": {}}) == "user"
        assert _resolve_role({"type": "assistant", "message": {}}) == "assistant"
        assert _resolve_role({"type": "system", "message": {}}) == "system"

    def test_resolve_role_claude_style(self) -> None:
        assert _resolve_role({"message": {"role": "user"}}) == "user"
        assert _resolve_role({"message": {"role": "assistant"}}) == "assistant"

    def test_resolve_role_unknown_returns_empty(self) -> None:
        assert _resolve_role({"foo": "bar"}) == ""

    def test_extract_compactions(self) -> None:
        lines = [json.loads(l) for l in SYNTHETIC_CLAUDE_JSONL.strip().split("\n")]
        compactions = _extract_compactions(lines)
        assert len(compactions) == 1
        assert "hello world" in compactions[0].lower()

    def test_build_exchanges_groups_user_and_assistant(self) -> None:
        lines = [json.loads(l) for l in SYNTHETIC_CLAUDE_JSONL.strip().split("\n")]
        exchanges = _build_exchanges(lines)
        # 2 user queries → 2 exchanges
        assert len(exchanges) == 2
        # First exchange: hello world
        assert "[User]:" in exchanges[0]
        assert "hello world" in exchanges[0].lower()
        assert "here is a hello world" in exchanges[0].lower()
        # Second exchange: docstring
        assert "docstring" in exchanges[1].lower()
        # Tool use content should not be in exchanges
        assert "tool_use" not in exchanges[0].lower()
        assert "tool_use" not in exchanges[1].lower()

    def test_build_exchanges_excludes_system_content(self) -> None:
        lines = [json.loads(l) for l in SYNTHETIC_CLAUDE_JSONL.strip().split("\n")]
        exchanges = _build_exchanges(lines)
        # System message content should not appear
        for ex in exchanges:
            assert "You are a coding agent" not in ex.lower()

    def test_find_model_from_system(self) -> None:
        lines = [
            {"type": "system", "message": {"role": "system", "model": "claude-sonnet-4-6"}},
            {"type": "user", "message": {"content": "hi"}},
        ]
        assert _find_model(lines) == "claude-sonnet-4-6"

    def test_extract_session_id(self) -> None:
        lines = [{"session_id": "abc-123"}]
        assert _extract_session_id(lines, "fallback") == "abc-123"

    def test_extract_session_id_fallback(self) -> None:
        assert _extract_session_id([], "myfile") == "session:myfile"

    def test_extract_created_at_accepts_millisecond_timestamp(self) -> None:
        lines = [{"timestamp": 1_770_887_989_262}]

        created_at = _extract_created_at(lines)

        assert created_at is not None
        assert created_at.startswith("2026-")

    def test_extract_created_at_accepts_second_timestamp(self) -> None:
        lines = [{"timestamp": 1_767_225_600}]

        created_at = _extract_created_at(lines)

        assert created_at == "2026-01-01T00:00:00+00:00"

    def test_extract_created_at_ignores_invalid_numeric_timestamp(self) -> None:
        lines = [{"timestamp": 10**30}]

        assert _extract_created_at(lines) is None

    def test_extract_created_at_continues_after_invalid_numeric_timestamp(self) -> None:
        lines = [
            {"timestamp": 10**30},
            {"created_at": "2026-01-01T00:00:00+00:00"},
        ]

        assert _extract_created_at(lines) == "2026-01-01T00:00:00+00:00"

    def test_empty_jsonl_yields_none(self) -> None:
        records = get_parser("agent_session")(Path("/nonexistent"), "p", "", "local_private")
        assert records == []

    def test_codex_style_parsing(self) -> None:
        lines = [json.loads(l) for l in SYNTHETIC_CODEX_JSONL.strip().split("\n")]
        exchanges = _build_exchanges(lines)
        assert len(exchanges) == 2
        assert "bug" in exchanges[0].lower()
        # tool_call should be filtered
        assert "tool_call" not in exchanges[0].lower()
        assert "tool_call" not in exchanges[1].lower()

    def test_realistic_codex_response_item_parsing(self) -> None:
        lines = [json.loads(l) for l in REALISTIC_CODEX_JSONL.strip().split("\n")]

        exchanges = _build_exchanges(lines)
        assert len(exchanges) == 1
        assert "Continue Truenex Memory and build ingestion." in exchanges[0]
        assert "Implemented ingestion manifest support." in exchanges[0]
        # Developer and tool_call content should not appear
        assert "Do not index developer instructions" not in exchanges[0]
        assert "tool_call" not in exchanges[0]

        assert _extract_compactions(lines) == [
            "Ingestion task summary with manifest and JSONL parser."
        ]
        assert _extract_session_id(lines, "fallback") == "sess-real"
        assert _find_model(lines) == "gpt-5.5"


# ── Engine ───────────────────────────────────────────────────────────

class TestIngestEngine:
    def test_dry_run_project_docs(self) -> None:
        wd = _workdir("eng_dryrun")
        _write_text_file(wd, "docs/readme.md", "# Project\n\nContent here.")
        manifest_path = _write_manifest(wd, [
            {"source_type": "project_docs", "source_path": "docs/"},
        ])

        config = resolve_project_config(wd)
        repo = MemoryRepository(config.db_path)

        report = ingest_manifest(manifest_path, wd, repo, dry_run=True)

        assert len(report["index_now"]) == 1
        assert len(report["parse_later"]) == 0
        assert len(report["skipped"]) == 0
        assert len(report["errors"]) == 0

        item = report["index_now"][0]
        assert item["source_type"] == "project_docs"
        assert "readme.md" in str(item["source_path"])
        assert item["chars"] > 0

        # Dry-run must not create local memory storage.
        assert not config.db_path.exists()

    def test_dry_run_parse_later_types(self) -> None:
        wd = _workdir("eng_parselater")
        manifest_path = _write_manifest(wd, [
            {"source_type": "agent_memory", "source_path": "memory/"},
            {"source_type": "operations_note", "source_path": "notes/"},
            {"source_type": "binary_document", "source_path": "bin/"},
        ])

        config = resolve_project_config(wd)
        repo = MemoryRepository(config.db_path)

        report = ingest_manifest(manifest_path, wd, repo, dry_run=True)

        assert len(report["index_now"]) == 0
        assert len(report["parse_later"]) == 3
        assert {r["source_type"] for r in report["parse_later"]} == PARSE_LATER_SOURCE_TYPES

    def test_dry_run_agent_session(self) -> None:
        wd = _workdir("eng_session")
        _write_text_file(wd, "sessions/session1.jsonl", SYNTHETIC_CLAUDE_JSONL)
        manifest_path = _write_manifest(wd, [
            {"source_type": "agent_session", "source_path": "sessions/", "source_tool": "claude-code"},
        ])

        config = resolve_project_config(wd)
        repo = MemoryRepository(config.db_path)

        report = ingest_manifest(manifest_path, wd, repo, dry_run=True)

        assert len(report["index_now"]) >= 1
        item = report["index_now"][0]
        assert item["source_type"] == "agent_session"
        assert item["session_id"] is not None

    def test_dry_run_missing_source_dir(self) -> None:
        wd = _workdir("eng_missing")
        manifest_path = _write_manifest(wd, [
            {"source_type": "project_docs", "source_path": "nonexistent/"},
        ])

        config = resolve_project_config(wd)
        repo = MemoryRepository(config.db_path)

        report = ingest_manifest(manifest_path, wd, repo, dry_run=True)
        assert len(report["index_now"]) == 0

    def test_dry_run_bad_manifest_path(self) -> None:
        wd = _workdir("eng_badman")
        config = resolve_project_config(wd)
        repo = MemoryRepository(config.db_path)

        report = ingest_manifest(Path("/nonexistent/manifest.json"), wd, repo, dry_run=True)
        assert len(report["errors"]) == 1
        assert "manifest not found" in str(report["errors"][0]["error"])

    def test_actual_indexing_indexes_documents(self) -> None:
        wd = _workdir("eng_real")
        _write_text_file(wd, "docs/readme.md", "# Test Project\n\nReal content for indexing.")
        manifest_path = _write_manifest(wd, [
            {"source_type": "project_docs", "source_path": "docs/", "source_tool": "markdown"},
        ])

        config = resolve_project_config(wd)
        repo = MemoryRepository(config.db_path)
        repo.initialize()

        report = ingest_manifest(manifest_path, wd, repo, dry_run=False)

        assert len(report["index_now"]) == 1
        stats = repo.stats()
        assert stats["documents"] == 1
        assert stats["chunks"] >= 1

        # Verify search works on indexed content
        results = repo.search("Test Project")
        assert len(results) > 0
        assert any("Real content" in r.content for r in results)

    def test_actual_indexing_agent_session(self) -> None:
        wd = _workdir("eng_session_real")
        _write_text_file(wd, "sessions/s1.jsonl", SYNTHETIC_CLAUDE_JSONL)
        manifest_path = _write_manifest(wd, [
            {"source_type": "agent_session", "source_path": "sessions/", "source_tool": "claude-code"},
        ])

        config = resolve_project_config(wd)
        repo = MemoryRepository(config.db_path)
        repo.initialize()

        report = ingest_manifest(manifest_path, wd, repo, dry_run=False)

        assert len(report["index_now"]) >= 1
        stats = repo.stats()
        assert stats["documents"] == 1
        assert stats["chunks"] >= 1

        # Verify search finds session content (exchanges share same doc_id,
        # last exchange overwrites, so search for any exchange content)
        results = repo.search("docstring")
        assert len(results) > 0
        # Source path should reference the session
        source_paths = [r.source_path for r in results]
        assert any("s1.jsonl" in (sp or "") for sp in source_paths)


# ── CLI command ──────────────────────────────────────────────────────

class TestCliIngestCommand:
    def test_ingest_manifest_help(self) -> None:
        result = runner.invoke(app, ["ingest", "manifest", "--help"])
        output = plain_cli_output(result.stdout)
        assert result.exit_code == 0
        assert "--manifest" in output
        assert "--dry-run" in output

    def test_dry_run_json_output(self) -> None:
        wd = _workdir("cli_dryrun")
        _write_text_file(wd, "docs/readme.md", "# Hello\n\nWorld.")
        manifest_path = _write_manifest(wd, [
            {"source_type": "project_docs", "source_path": "docs/"},
        ])

        with _cwd(wd):
            result = runner.invoke(app, [
                "ingest", "manifest",
                "--manifest", str(manifest_path),
                "--dry-run",
                "--json",
            ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "index_now" in payload
        assert len(payload["index_now"]) == 1
        assert payload["index_now"][0]["source_type"] == "project_docs"
        assert not (wd / ".truenex-memory" / "truenex_memory.db").exists()

    def test_dry_run_text_output(self) -> None:
        wd = _workdir("cli_dryrun_txt")
        _write_text_file(wd, "docs/readme.md", "Content")
        manifest_path = _write_manifest(wd, [
            {"source_type": "project_docs", "source_path": "docs/"},
        ])

        with _cwd(wd):
            result = runner.invoke(app, [
                "ingest", "manifest",
                "--manifest", str(manifest_path),
                "--dry-run",
            ])

        assert result.exit_code == 0
        assert "DRY-RUN REPORT" in result.stdout
        assert "readme.md" in result.stdout
        assert not (wd / ".truenex-memory" / "truenex_memory.db").exists()

    def test_parse_later_types_in_cli(self) -> None:
        wd = _workdir("cli_parselater")
        manifest_path = _write_manifest(wd, [
            {"source_type": "binary_document", "source_path": "bin/"},
        ])

        with _cwd(wd):
            runner.invoke(app, ["init"])
            result = runner.invoke(app, [
                "ingest", "manifest",
                "--manifest", str(manifest_path),
                "--dry-run",
                "--json",
            ])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload["parse_later"]) == 1
        assert payload["parse_later"][0]["source_type"] == "binary_document"

    def test_manifest_file_must_exist(self) -> None:
        wd = _workdir("cli_nomanifest")
        with _cwd(wd):
            runner.invoke(app, ["init"])
            result = runner.invoke(app, [
                "ingest", "manifest",
                "--manifest", "nonexistent.json",
            ])
        # Typer validates --exists and exits non-zero
        assert result.exit_code != 0

    def test_actual_ingestion_via_cli(self) -> None:
        wd = _workdir("cli_real")
        _write_text_file(wd, "docs/api.md", "# API\n\nEndpoints.")
        manifest_path = _write_manifest(wd, [
            {"source_type": "project_docs", "source_path": "docs/"},
        ])

        with _cwd(wd):
            runner.invoke(app, ["init"])
            result = runner.invoke(app, [
                "ingest", "manifest",
                "--manifest", str(manifest_path),
            ])

        assert result.exit_code == 0
        assert "INGEST REPORT" in result.stdout
        assert "api.md" in result.stdout

        # Search to confirm indexing
        with _cwd(wd):
            search_result = runner.invoke(app, [
                "search", "Endpoints", "--top-k", "3", "--json",
            ])
        search_payload = json.loads(search_result.stdout)
        assert search_payload["results"]
        assert any("api.md" in (r.get("source_path") or "") for r in search_payload["results"])


# ── Parser registry ──────────────────────────────────────────────────

class TestParserRegistry:
    def test_all_indexable_types_have_parsers(self) -> None:
        registered = set(parsers().keys())
        for source_type in INDEXABLE_SOURCE_TYPES:
            assert source_type in registered, f"missing parser for {source_type!r}"

    def test_unknown_type_returns_none(self) -> None:
        assert get_parser("nonexistent_type") is None

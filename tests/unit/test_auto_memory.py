"""Tests for Phase 3 Auto Memory external vector gating and Phase 3B features."""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from truenex_memory.core.chunker import chunk_text
from truenex_memory.discovery.source_catalog import source_id
from truenex_memory.ingestion.global_auto_memory import (
    AGENT_SESSION_CONFIDENCE,
    COMPACTION_CONFIDENCE,
    AutoMemoryCandidate,
    _RE_IS_COMPACTION,
    _agent_session_title,
    _is_raw_json_dump,
    _is_noisy_agent_session_candidate,
    _iter_candidates,
    _sort_key_for_candidate,
    generate_unverified_auto_memories,
)
from truenex_memory.ingestion.global_refresh import RefreshReport
from truenex_memory.retrieval.semantic import HashingEmbedder, VectorMatch, VectorPoint
from truenex_memory.store.repository import MemoryRepository
from truenex_memory.store.source_ledger import upsert_ledger_entry
from truenex_memory.store.sqlite import connect, initialize_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StaticVectorStore:
    """Small vector store that returns stored points in insertion order."""

    def __init__(self) -> None:
        self.points: list[VectorPoint] = []

    def upsert(self, points: list[VectorPoint]) -> None:
        self.points.extend(points)

    def search(self, vector: list[float], *, top_k: int) -> list[VectorMatch]:
        return [
            VectorMatch(point_id=point.point_id, score=0.9)
            for point in self.points[:top_k]
        ]


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parents[1] / "unit" / f"task_work_auto_{name}_{uuid.uuid4().hex}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _mark_ledger(db_path: Path, source_path: str, status: str) -> None:
    with connect(db_path) as conn:
        initialize_schema(conn)
        upsert_ledger_entry(
            conn,
            source_id("project_docs", source_path),
            source_path,
            "project_docs",
            project_name="proj",
            status=status,
            content_hash="hash",
            chunk_count=1,
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_session_chunk(
    db_path: Path,
    qualified_path: str,
    chunk_content: str,
    ledger_status: str = "active",
) -> None:
    """Insert one agent_session document + chunk + ledger entry using the real schema."""
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    now = _now_iso()
    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (id, project_id, path, filename, content_hash, last_indexed_at, created_at, updated_at)
            VALUES (?, 'default', ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, qualified_path, Path(qualified_path.split("::")[0]).name, "hash", now, now, now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO chunks
                (id, document_id, chunk_index, heading_path, content, content_hash, token_count, created_at, updated_at)
            VALUES (?, ?, 0, NULL, ?, ?, 10, ?, ?)
            """,
            (chunk_id, doc_id, chunk_content, "hash", now, now),
        )
        upsert_ledger_entry(
            conn,
            source_id("agent_session", qualified_path),
            qualified_path,
            "agent_session",
            project_name="test",
            status=ledger_status,
            content_hash="deadbeef",
            chunk_count=1,
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Original gate test (kept)
# ---------------------------------------------------------------------------

def test_external_vector_results_exclude_missing_ledger_chunks() -> None:
    wd = _workdir("external_vector_gate")
    db_path = wd / "memory.db"
    missing_doc = wd / "missing-external.md"
    missing_doc.write_text("# Missing\n\nexternal vector missing source", encoding="utf-8")
    vector_store = StaticVectorStore()

    repo = MemoryRepository(db_path, embedder=HashingEmbedder(), vector_store=vector_store)
    repo.upsert_document(missing_doc, str(missing_doc.resolve()), chunk_text(missing_doc.read_text()))
    _mark_ledger(db_path, str(missing_doc.resolve()), "missing")

    results = repo.search("terms absent so only external vector can match", top_k=5)

    assert results == []


# ---------------------------------------------------------------------------
# Bug 6 fix: extension filter must not block agent_session chunks
# ---------------------------------------------------------------------------

def test_agent_session_chunks_not_filtered_by_extension() -> None:
    """Chunks whose source_type is agent_session must reach the candidate list
    even when the source path ends in .jsonl (not in AUTO_MEMORY_EXTENSIONS)."""
    wd = _workdir("bug6_ext_filter")
    db_path = wd / "memory.db"
    qualified_path = str(wd / "session.jsonl::exchange_0")
    chunk_content = (
        "TRUENEX_INGESTION_METADATA "
        '{"exchange_index": 0, "is_compaction": false}\n\n'
        "This is a meaningful exchange about configuration setup with enough words to pass the token filter."
    )

    _insert_session_chunk(db_path, qualified_path, chunk_content)

    report = RefreshReport()
    generate_unverified_auto_memories(db_path, report, dry_run=True)

    assert report.auto_memory_non_document_skipped == 0
    assert report.auto_memory_candidates >= 1


def test_noisy_agent_session_candidates_are_skipped_before_memory_creation() -> None:
    wd = _workdir("noisy_session_skip")
    db_path = wd / "memory.db"
    noisy_path = str(wd / "session.jsonl::exchange_0")
    useful_path = str(wd / "session.jsonl::exchange_1")
    _insert_session_chunk(
        db_path,
        noisy_path,
        (
            "TRUENEX_INGESTION_METADATA "
            '{"exchange_index": 0, "is_compaction": false}\n\n'
            "[User]: please run this raw command transcript and do not promote it."
        ),
    )
    _insert_session_chunk(
        db_path,
        useful_path,
        (
            "TRUENEX_INGESTION_METADATA "
            '{"exchange_index": 1, "is_compaction": true}\n\n'
            "Project summary: Truenex Memory uses local SQLite and Qdrant for agent context."
        ),
    )

    report = RefreshReport()
    generate_unverified_auto_memories(db_path, report, dry_run=True)

    assert report.auto_memory_noisy_session_skipped == 1
    assert report.auto_memory_candidates == 1
    assert report.auto_memory_created == 1


def test_noisy_agent_session_detector_flags_known_noise_shapes() -> None:
    samples = [
        'Continue the conversation from where it left off without asking the user.',
        '6. All user messages:\n- "cosa e successo?"',
        '[Assistant]: I will inspect the file and run tests.',
        '1. Check status: `git status --short`\n2. Copy files: `cp -r dist/* /var/www/app/`',
    ]
    for sample in samples:
        assert _is_noisy_agent_session_candidate(sample)


def test_noisy_agent_session_detector_flags_transcript_line_after_heading() -> None:
    sample = (
        "Session: 2026-05-07\n"
        "[User]: please add the feature in this raw transcript fragment."
    )
    assert _is_noisy_agent_session_candidate(sample)


def test_noisy_agent_session_detector_allows_distilled_summaries() -> None:
    sample = (
        "The user's overarching goal is Phase 0 to Phase 1 transition for "
        "TrueNex Local QVAC MedPsy: offline-first desktop clinical documentation."
    )
    assert not _is_noisy_agent_session_candidate(sample)


def test_noisy_agent_session_detector_allows_numbered_decision_summaries() -> None:
    sample = (
        "1. Decision: use `git` for version control and `docker` for deployment.\n"
        "2. Decision: keep `npm` workspaces for frontend packages.\n"
        "3. Rationale: the team already has those tools in the project workflow."
    )
    assert not _is_noisy_agent_session_candidate(sample)


# ---------------------------------------------------------------------------
# Feature 1: compaction records get higher confidence
# ---------------------------------------------------------------------------

def test_compaction_gets_higher_confidence() -> None:
    """Chunks with is_compaction=true in the TRUENEX_INGESTION_METADATA preamble
    must receive COMPACTION_CONFIDENCE (0.75), normal exchanges AGENT_SESSION_CONFIDENCE (0.60)."""
    compaction_chunk = (
        'TRUENEX_INGESTION_METADATA {"exchange_index": 389, "is_compaction": true, "session_line_count": 1234}\n\n'
        "Long session summary."
    )
    normal_chunk = (
        'TRUENEX_INGESTION_METADATA {"exchange_index": 5, "is_compaction": false}\n\n'
        "User asked about architecture."
    )

    assert _RE_IS_COMPACTION.search(compaction_chunk) is not None
    assert _RE_IS_COMPACTION.search(normal_chunk) is None
    assert COMPACTION_CONFIDENCE > AGENT_SESSION_CONFIDENCE
    assert COMPACTION_CONFIDENCE == 0.75
    assert AGENT_SESSION_CONFIDENCE == 0.60


def test_compaction_confidence_assigned_in_iter_candidates() -> None:
    """End-to-end: _iter_candidates assigns 0.75 to compaction, 0.60 to normal exchange."""
    wd = _workdir("feat1_confidence")
    db_path = wd / "memory.db"

    compaction_content = (
        "TRUENEX_INGESTION_METADATA "
        '{"exchange_index": 389, "is_compaction": true}\n\n'
        + "A " * 60  # well above MIN_CANDIDATE_TOKENS
    )
    normal_content = (
        "TRUENEX_INGESTION_METADATA "
        '{"exchange_index": 5, "is_compaction": false}\n\n'
        + "B " * 20
    )

    qpath_c = str(wd / "session.jsonl::exchange_389")
    qpath_n = str(wd / "session.jsonl::exchange_5")

    _insert_session_chunk(db_path, qpath_c, compaction_content)
    _insert_session_chunk(db_path, qpath_n, normal_content)

    candidates = _iter_candidates(db_path)
    by_path = {c.source_path: c for c in candidates}

    assert by_path[qpath_c].confidence == COMPACTION_CONFIDENCE
    assert by_path[qpath_c].is_compaction is True
    assert by_path[qpath_n].confidence == AGENT_SESSION_CONFIDENCE
    assert by_path[qpath_n].is_compaction is False


# ---------------------------------------------------------------------------
# Feature 2: title generation for agent_session candidates
# ---------------------------------------------------------------------------

def test_compaction_title_prefixed() -> None:
    """_agent_session_title must return a title starting with 'Session Summary:' for compaction records."""
    chunk = (
        "TRUENEX_INGESTION_METADATA "
        '{"exchange_index": 389, "is_compaction": true, "created_at": "2026-04-10T14:30:00Z"}\n\n'
        "Long summary text."
    )
    title = _agent_session_title("/some/path/session.jsonl::exchange_389", chunk)
    assert title.startswith("Session Summary:"), f"unexpected title: {title!r}"
    assert "2026-04-10" in title


def test_normal_exchange_title_has_snippet() -> None:
    """_agent_session_title for a normal exchange must include exchange number and snippet."""
    chunk = (
        "TRUENEX_INGESTION_METADATA "
        '{"exchange_index": 7, "is_compaction": false}\n\n'
        "The user asked about deployment configuration for the staging environment."
    )
    title = _agent_session_title("/some/path/session.jsonl::exchange_7", chunk)
    assert "7" in title
    assert "deployment" in title.lower() or "Exchange" in title


def test_title_falls_back_to_filename_date() -> None:
    """When metadata has no date, extract date from the filename stem."""
    chunk = (
        "TRUENEX_INGESTION_METADATA "
        '{"exchange_index": 0, "is_compaction": true}\n\n'
        "Summary content."
    )
    title = _agent_session_title("/sessions/rollout-2026-05-02T12:30:00.jsonl::exchange_0", chunk)
    assert title.startswith("Session Summary:")
    assert "2026-05-02" in title


# ---------------------------------------------------------------------------
# Feature 3: sort key — compaction before normal, longer text first
# ---------------------------------------------------------------------------

def test_sort_key_compaction_before_normal() -> None:
    """Compaction candidates must sort before normal ones regardless of token count."""
    compaction = AutoMemoryCandidate(
        content="A" * 200,
        title="t",
        source_path="s",
        source_document_id="1",
        source_chunk_id="1",
        source_type="agent_session",
        confidence=0.75,
        is_compaction=True,
    )
    normal = AutoMemoryCandidate(
        content="B" * 500,  # longer than compaction, but not compaction
        title="t",
        source_path="s",
        source_document_id="2",
        source_chunk_id="2",
        source_type="agent_session",
        confidence=0.60,
        is_compaction=False,
    )
    assert _sort_key_for_candidate(compaction) < _sort_key_for_candidate(normal)


def test_sort_key_longer_normal_before_shorter() -> None:
    """Among non-compaction candidates, longer token count sorts first."""
    long_ex = AutoMemoryCandidate(
        content="word " * 50,
        title="t",
        source_path="s",
        source_document_id="1",
        source_chunk_id="1",
        source_type="agent_session",
        confidence=0.60,
        is_compaction=False,
    )
    short_ex = AutoMemoryCandidate(
        content="word " * 5,
        title="t",
        source_path="s",
        source_document_id="2",
        source_chunk_id="2",
        source_type="agent_session",
        confidence=0.60,
        is_compaction=False,
    )
    assert _sort_key_for_candidate(long_ex) < _sort_key_for_candidate(short_ex)


# ---------------------------------------------------------------------------
# Bug 5 fix: JOIN uses qualified path
# ---------------------------------------------------------------------------

def test_iter_candidates_join_uses_qualified_path() -> None:
    """After Bug 5 fix, a document with path session.jsonl::exchange_7 matched by a
    ledger entry with the same qualified source_path_or_alias is returned by _iter_candidates."""
    wd = _workdir("bug5_join_qualified")
    db_path = wd / "memory.db"
    qualified_path = str(wd / "session.jsonl::exchange_7")
    chunk_content = (
        "TRUENEX_INGESTION_METADATA "
        '{"exchange_index": 7, "is_compaction": false}\n\n'
        "Important architectural decisions about the system were discussed in depth here."
    )

    _insert_session_chunk(db_path, qualified_path, chunk_content)

    candidates = _iter_candidates(db_path)

    assert len(candidates) == 1, f"expected 1 candidate, got {len(candidates)}"
    assert candidates[0].source_path == qualified_path
    assert candidates[0].source_type == "agent_session"


def test_iter_candidates_plain_path_join_fails() -> None:
    """Sanity: if ledger uses plain path but documents uses qualified path, JOIN produces zero rows."""
    wd = _workdir("bug5_join_plain_fails")
    db_path = wd / "memory.db"
    plain_path = str(wd / "session.jsonl")
    qualified_path = str(wd / "session.jsonl::exchange_7")
    chunk_content = "Some content about the session with enough tokens to pass the minimum filter here."

    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    now = _now_iso()
    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (id, project_id, path, filename, content_hash, last_indexed_at, created_at, updated_at)
            VALUES (?, 'default', ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, qualified_path, "session.jsonl", "hash", now, now, now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO chunks
                (id, document_id, chunk_index, content, content_hash, token_count, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?, 10, ?, ?)
            """,
            (chunk_id, doc_id, chunk_content, "hash", now, now),
        )
        # Ledger uses plain path — mismatches documents.path → JOIN finds nothing.
        upsert_ledger_entry(
            conn,
            source_id("agent_session", plain_path),
            plain_path,
            "agent_session",
            project_name="test",
            status="active",
            content_hash="deadbeef",
            chunk_count=1,
        )
        conn.commit()

    candidates = _iter_candidates(db_path)
    assert len(candidates) == 0, "plain-path ledger must not join with qualified-path document"


# ---------------------------------------------------------------------------
# Fix 1: raw JSON dump filter
# ---------------------------------------------------------------------------

def test_raw_json_dump_skipped() -> None:
    """_is_raw_json_dump must return True for Codex-style tool-result JSON text."""
    json_texts = [
        '{"type": "event_msg", "payload": {"type": "exec_command_end"}}',
        '{"timestamp":"2026-05-08T10:00:00Z","type":"event_msg","payload":{}}',
        '  {"type":"function_call_output","call_id":"abc123"}',
    ]
    for text in json_texts:
        assert _is_raw_json_dump(text), f"expected JSON dump to be detected: {text[:60]!r}"


def test_raw_json_dump_normal_text_passes() -> None:
    """_is_raw_json_dump must return False for normal readable text."""
    normal_texts = [
        "The architecture uses a dual-model router with Mistral 24B.",
        "# Project Notes\n\nThis document describes the ingestion pipeline.",
        "User asked about deployment configuration for the staging environment.",
        "",
    ]
    for text in normal_texts:
        assert not _is_raw_json_dump(text), f"normal text incorrectly flagged: {text[:60]!r}"


# ---------------------------------------------------------------------------
# Fix 2: global sort — compaction < session < doc
# ---------------------------------------------------------------------------

def test_global_sort_compaction_before_session_before_doc() -> None:
    """Sort key ordering: compaction (0,0,*) < session (1,0,*) < doc (1,1,*)."""
    compaction = AutoMemoryCandidate(
        content="summary " * 30,
        title="t",
        source_path="s",
        source_document_id="1",
        source_chunk_id="1",
        source_type="agent_session",
        confidence=0.75,
        is_compaction=True,
    )
    session = AutoMemoryCandidate(
        content="exchange " * 20,
        title="t",
        source_path="s",
        source_document_id="2",
        source_chunk_id="2",
        source_type="agent_session",
        confidence=0.60,
        is_compaction=False,
    )
    doc = AutoMemoryCandidate(
        content="documentation " * 20,
        title="t",
        source_path="s",
        source_document_id="3",
        source_chunk_id="3",
        source_type="project_docs",
        confidence=0.80,
        is_compaction=False,
    )
    assert _sort_key_for_candidate(compaction) < _sort_key_for_candidate(session)
    assert _sort_key_for_candidate(session) < _sort_key_for_candidate(doc)
    assert _sort_key_for_candidate(compaction) < _sort_key_for_candidate(doc)


def test_global_sort_applied_in_iter_candidates() -> None:
    """_iter_candidates must return chunks ordered: compaction, session, doc."""
    wd = _workdir("global_sort_order")
    db_path = wd / "memory.db"

    compaction_content = (
        "TRUENEX_INGESTION_METADATA "
        '{"exchange_index": 100, "is_compaction": true}\n\n'
        + "summary " * 30
    )
    session_content = (
        "TRUENEX_INGESTION_METADATA "
        '{"exchange_index": 5, "is_compaction": false}\n\n'
        + "exchange " * 20
    )
    doc_content = "documentation about project architecture " * 10

    qpath_compaction = str(wd / "session.jsonl::exchange_100")
    qpath_session = str(wd / "session.jsonl::exchange_5")

    _insert_session_chunk(db_path, qpath_compaction, compaction_content)
    _insert_session_chunk(db_path, qpath_session, session_content)

    # Insert a project_docs chunk with its own ledger entry.
    doc_path = wd / "README.md"
    doc_path.write_text(doc_content, encoding="utf-8")
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    now = _now_iso()
    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (id, project_id, path, filename, content_hash, last_indexed_at, created_at, updated_at)
            VALUES (?, 'default', ?, 'README.md', 'hash', ?, ?, ?)
            """,
            (doc_id, str(doc_path), now, now, now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO chunks
                (id, document_id, chunk_index, content, content_hash, token_count, created_at, updated_at)
            VALUES (?, ?, 0, ?, 'hash', 80, ?, ?)
            """,
            (chunk_id, doc_id, doc_content, now, now),
        )
        upsert_ledger_entry(
            conn,
            source_id("project_docs", str(doc_path)),
            str(doc_path),
            "project_docs",
            project_name="test",
            status="active",
            content_hash="deadbeef",
            chunk_count=1,
        )
        conn.commit()

    candidates = _iter_candidates(db_path)

    assert len(candidates) == 3, f"expected 3 candidates, got {len(candidates)}"
    assert candidates[0].is_compaction is True, "first candidate must be compaction"
    assert candidates[1].source_type == "agent_session" and not candidates[1].is_compaction
    assert candidates[2].source_type == "project_docs"

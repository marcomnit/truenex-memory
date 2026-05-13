"""Conservative unverified auto-memory generation for Phase 3.4."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import sqlite3

from truenex_memory.core.chunker import content_hash, estimate_tokens
from truenex_memory.ingestion.global_refresh import RefreshReport
from truenex_memory.store.repository import MemoryRepository


PROJECT_DOCS_CONFIDENCE = 0.80
AGENT_SESSION_CONFIDENCE = 0.60
COMPACTION_CONFIDENCE = 0.75
DEFAULT_CONFIDENCE = 0.50
MIN_CANDIDATE_TOKENS = 8
DEFAULT_AUTO_MEMORY_LIMIT = 300
DEFAULT_AUTO_MEMORY_PER_SOURCE_LIMIT = 8
AUTO_MEMORY_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".rst"})

# Regex to detect compaction flag in TRUENEX_INGESTION_METADATA preamble without
# full JSON parse — "is_compaction": true can appear anywhere in the JSON object.
_RE_IS_COMPACTION = re.compile(r'"is_compaction"\s*:\s*true')
_RE_METADATA_LINE = re.compile(r'^TRUENEX_INGESTION_METADATA\s+(\{.*\})', re.MULTILINE)
_RE_JSON_DUMP = re.compile(r'^\s*\{.*"type"\s*:', re.DOTALL)
_RE_AGENT_TRANSCRIPT_LINE = re.compile(r'(^|\n)\s*\[(?:user|assistant)\]:', re.IGNORECASE)
_RE_ALL_USER_MESSAGES = re.compile(r'(^|\n)\s*\d+\.\s+all user messages\s*:', re.IGNORECASE)
_RE_NUMBERED_COMMAND_LINE = re.compile(
    r'^\s*\d+\.\s*'
    r'(?:build|check|copy|deploy|execute|find|lancia|mostra|open|restore|run|'
    r'show|start|stop|trova|verifica|verify)\b'
    r'.*`[^`]*(?:cargo|cp|docker|find|git|grep|ls|npm|powershell|ssh)[^`]*`',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AutoMemoryCandidate:
    """A source-grounded candidate for an unverified memory node."""

    content: str
    title: str
    source_path: str
    source_document_id: str
    source_chunk_id: str
    source_type: str | None
    confidence: float
    is_compaction: bool


@dataclass(frozen=True)
class AutoMemoryTelemetry:
    """Read-only candidate quality counters for Auto Memory."""

    candidates: int = 0
    duplicate_skips: int = 0
    duplicate_active: int = 0
    duplicate_unverified: int = 0
    duplicate_rejected: int = 0
    low_confidence: int = 0
    non_document_skipped: int = 0
    noisy_session_skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "candidates": self.candidates,
            "duplicate_skips": self.duplicate_skips,
            "duplicate_active": self.duplicate_active,
            "duplicate_unverified": self.duplicate_unverified,
            "duplicate_rejected": self.duplicate_rejected,
            "low_confidence": self.low_confidence,
            "non_document_skipped": self.non_document_skipped,
            "noisy_session_skipped": self.noisy_session_skipped,
        }


def generate_unverified_auto_memories(
    db_path: Path,
    report: RefreshReport,
    *,
    dry_run: bool,
    min_confidence: float = DEFAULT_CONFIDENCE,
    limit: int = DEFAULT_AUTO_MEMORY_LIMIT,
    per_source_limit: int = DEFAULT_AUTO_MEMORY_PER_SOURCE_LIMIT,
) -> None:
    """Generate exact-deduped unverified memory nodes from active chunks."""
    if not db_path.exists():
        return

    repository = MemoryRepository(db_path)
    created_or_planned = 0
    created_or_planned_by_source: dict[str, int] = {}
    blocked_hashes = _blocked_auto_memory_content_hashes_by_reason(db_path)
    for candidate in _iter_candidates(db_path):
        # agent_session chunks are always valid candidates regardless of extension.
        if candidate.source_type != "agent_session":
            if Path(candidate.source_path).suffix.lower() not in AUTO_MEMORY_EXTENSIONS:
                report.auto_memory_non_document_skipped += 1
                continue
        elif _is_noisy_agent_session_candidate(candidate.content):
            report.auto_memory_noisy_session_skipped += 1
            continue
        report.auto_memory_candidates += 1
        candidate_hash = content_hash(candidate.content)
        duplicate_reason = blocked_hashes.get(candidate_hash)
        if duplicate_reason:
            report.auto_memory_duplicates += 1
            _count_duplicate_reason(report, duplicate_reason)
            continue
        if candidate.confidence < min_confidence:
            report.auto_memory_low_confidence += 1
            continue
        source_count = created_or_planned_by_source.get(candidate.source_path, 0)
        if per_source_limit > 0 and source_count >= per_source_limit:
            report.auto_memory_source_limit_skipped += 1
            continue
        if limit > 0 and created_or_planned >= limit:
            report.auto_memory_limit_skipped += 1
            continue
        created_or_planned += 1
        created_or_planned_by_source[candidate.source_path] = source_count + 1
        report.auto_memory_created += 1
        if dry_run:
            continue
        repository.add_memory(
            candidate.content,
            memory_type="note",
            title=candidate.title,
            status="unverified",
            source_kind="auto",
            source_document_id=candidate.source_document_id,
            source_chunk_id=candidate.source_chunk_id,
            source_path=candidate.source_path,
            created_by="auto",
            confidence=candidate.confidence,
        )
        blocked_hashes[candidate_hash] = "unverified"


def analyze_auto_memory_candidates(
    db_path: Path,
    *,
    min_confidence: float = DEFAULT_CONFIDENCE,
) -> AutoMemoryTelemetry:
    """Return read-only Auto Memory candidate quality counters."""
    if not db_path.exists():
        return AutoMemoryTelemetry()

    counts = {
        "candidates": 0,
        "duplicate_skips": 0,
        "duplicate_active": 0,
        "duplicate_unverified": 0,
        "duplicate_rejected": 0,
        "low_confidence": 0,
        "non_document_skipped": 0,
        "noisy_session_skipped": 0,
    }
    try:
        blocked_hashes = _blocked_auto_memory_content_hashes_by_reason(db_path)
        candidates = _iter_candidates(db_path)
    except sqlite3.DatabaseError:
        return AutoMemoryTelemetry()

    for candidate in candidates:
        if candidate.source_type != "agent_session":
            if Path(candidate.source_path).suffix.lower() not in AUTO_MEMORY_EXTENSIONS:
                counts["non_document_skipped"] += 1
                continue
        elif _is_noisy_agent_session_candidate(candidate.content):
            counts["noisy_session_skipped"] += 1
            continue

        counts["candidates"] += 1
        candidate_hash = content_hash(candidate.content)
        duplicate_reason = blocked_hashes.get(candidate_hash)
        if duplicate_reason:
            counts["duplicate_skips"] += 1
            counts[f"duplicate_{duplicate_reason}"] += 1
            continue
        if candidate.confidence < min_confidence:
            counts["low_confidence"] += 1

    return AutoMemoryTelemetry(**counts)


def _iter_candidates(db_path: Path) -> list[AutoMemoryCandidate]:
    with _connect_readonly(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              c.id AS chunk_id,
              c.document_id,
              c.heading_path,
              c.content,
              d.path AS source_path,
              sl.source_type
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN source_ledger sl ON sl.source_path_or_alias = d.path
            WHERE sl.status = 'active'
            ORDER BY d.path, c.chunk_index
            """
        ).fetchall()

    candidates: list[AutoMemoryCandidate] = []
    for row in rows:
        raw_content = str(row["content"])
        is_compaction = bool(_RE_IS_COMPACTION.search(raw_content))
        text = _candidate_content(raw_content)
        if _is_raw_json_dump(text):
            continue
        if estimate_tokens(text) < MIN_CANDIDATE_TOKENS:
            continue
        source_type = row["source_type"]
        source_path = str(row["source_path"])
        if source_type == "agent_session":
            title = _agent_session_title(source_path, raw_content)
            confidence = COMPACTION_CONFIDENCE if is_compaction else AGENT_SESSION_CONFIDENCE
        else:
            title = str(row["heading_path"] or Path(source_path).name)
            confidence = _confidence_for_source_type(source_type)
            is_compaction = False
        candidates.append(
            AutoMemoryCandidate(
                content=text,
                title=title,
                source_path=source_path,
                source_document_id=str(row["document_id"]),
                source_chunk_id=str(row["chunk_id"]),
                source_type=str(source_type) if source_type is not None else None,
                confidence=confidence,
                is_compaction=is_compaction,
            )
        )

    # Prioritise compaction records first, then longest exchange text, so that
    # per_source_limit slots are filled with the most informative chunks.
    candidates.sort(key=_sort_key_for_candidate)
    return candidates


def _sort_key_for_candidate(c: AutoMemoryCandidate) -> tuple[int, int, int]:
    """Compaction first, then agent_session before static docs, then descending token count."""
    source_priority = 0 if c.source_type == "agent_session" else 1
    return (0 if c.is_compaction else 1, source_priority, -estimate_tokens(c.content))


def _agent_session_title(source_path: str, chunk_content: str) -> str:
    """Build a human-readable title for an agent-session memory candidate."""
    meta_match = _RE_METADATA_LINE.search(chunk_content)
    session_id: str | None = None
    created_at: str | None = None
    is_compaction = False
    exchange_index: int | None = None

    if meta_match:
        try:
            meta = json.loads(meta_match.group(1))
            session_id = meta.get("session_id")
            created_at = meta.get("created_at") or meta.get("last_modified")
            is_compaction = bool(meta.get("is_compaction"))
            exchange_index = meta.get("exchange_index")
        except (json.JSONDecodeError, AttributeError):
            pass

    # Date portion: prefer ISO timestamp truncated to date, fall back to filename.
    date_str: str = ""
    if created_at:
        date_str = str(created_at)[:10]
    elif session_id:
        # session_id often encodes a timestamp: take first 10 chars if digit-like
        candidate_date = re.search(r'\d{4}-\d{2}-\d{2}', str(session_id))
        date_str = candidate_date.group(0) if candidate_date else ""

    if not date_str:
        stem = Path(source_path.split("::")[0]).stem
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', stem)
        date_str = date_match.group(0) if date_match else stem

    if is_compaction:
        return f"Session Summary: {date_str}" if date_str else "Session Summary"

    # For normal exchanges use first 60 chars of user-visible text as suffix.
    text_after_meta = _candidate_content(chunk_content)
    snippet = text_after_meta[:60].replace("\n", " ").strip()
    n = f"#{exchange_index}" if exchange_index is not None else ""
    prefix = f"Session Exchange {n}: " if n else "Session Exchange: "
    return f"{prefix}{snippet}" if snippet else f"Session Exchange {n} ({date_str})"


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri_path = db_path.resolve().as_posix()
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _blocked_auto_memory_content_hashes_by_reason(db_path: Path) -> dict[str, str]:
    """Return content hashes that suppress auto memories, grouped by reason."""
    with _connect_readonly(db_path) as conn:
        rows = conn.execute(
            """
            SELECT content_hash, status, source_kind, created_by
            FROM memory_nodes
            WHERE project_id = 'default'
              AND (
                status IN ('active', 'unverified')
                OR (status = 'obsolete' AND source_kind = 'auto' AND created_by = 'auto')
              )
            ORDER BY created_at, id
            """
        ).fetchall()
    blocked: dict[str, str] = {}
    for row in rows:
        row_hash = row["content_hash"]
        if not row_hash:
            continue
        reason = _duplicate_reason_for_row(row)
        existing = blocked.get(str(row_hash))
        if (
            existing is None
            or _duplicate_reason_priority(reason) < _duplicate_reason_priority(existing)
        ):
            blocked[str(row_hash)] = reason
    return blocked


def _duplicate_reason_for_row(row: sqlite3.Row) -> str:
    status = row["status"]
    if status == "active":
        return "active"
    if status == "unverified":
        return "unverified"
    return "rejected"


def _duplicate_reason_priority(reason: str) -> int:
    return {"active": 0, "unverified": 1, "rejected": 2}.get(reason, 99)


def _count_duplicate_reason(report: RefreshReport, reason: str) -> None:
    if reason == "active":
        report.auto_memory_duplicate_active += 1
    elif reason == "unverified":
        report.auto_memory_duplicate_unverified += 1
    elif reason == "rejected":
        report.auto_memory_duplicate_rejected += 1


def _candidate_content(chunk_content: str) -> str:
    """Strip ingestion metadata preamble from the first indexed chunk."""
    text = chunk_content.strip()
    if text.startswith("TRUENEX_INGESTION_METADATA "):
        parts = re.split(r"\n\s*\n", text, maxsplit=1)
        if len(parts) == 2:
            return parts[1].strip()
        return ""
    return text


def _is_raw_json_dump(text: str) -> bool:
    return bool(_RE_JSON_DUMP.match(text[:300]))


def _is_noisy_agent_session_candidate(text: str) -> bool:
    """Return True for transcript fragments that should stay indexed, not promoted.

    Agent sessions are useful as source-grounded chunks, but generated memory
    nodes should capture distilled facts. Raw turn text, resume wrappers,
    message inventories, and command-only snippets create noisy global memory.
    """
    clean = text.strip()
    lowered = clean.lower()
    if "continue the conversation from where it left off" in lowered:
        return True
    if _RE_ALL_USER_MESSAGES.search(clean):
        return True
    if _RE_AGENT_TRANSCRIPT_LINE.search(clean):
        return True
    if _looks_like_command_snippet(clean):
        return True
    return False


def _looks_like_command_snippet(text: str) -> bool:
    numbered_lines = [
        line.strip()
        for line in text.splitlines()
        if re.match(r'^\s*\d+\.', line)
    ]
    if len(numbered_lines) < 2:
        return False
    command_lines = [
        line for line in numbered_lines if _RE_NUMBERED_COMMAND_LINE.match(line)
    ]
    return len(command_lines) >= 2 and (len(command_lines) / len(numbered_lines)) >= 0.66


def _confidence_for_source_type(source_type: object) -> float:
    if source_type == "project_docs":
        return PROJECT_DOCS_CONFIDENCE
    if source_type == "agent_session":
        return AGENT_SESSION_CONFIDENCE
    return DEFAULT_CONFIDENCE

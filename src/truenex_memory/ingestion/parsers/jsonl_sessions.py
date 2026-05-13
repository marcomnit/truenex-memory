"""Parser for Codex/Claude-style agent JSONL session logs.

Handles source_type=agent_session. Walks a directory for .jsonl files,
extracts a human-readable digest per file:

- Session metadata (model, timestamps)
- User requests
- Assistant final text responses
- Compaction / summary entries

Raw tool dumps, system/developer instructions, and intermediate API
payloads are intentionally excluded to keep the index lean and private-safe.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from truenex_memory.ingestion.manifest import IngestionRecord
from truenex_memory.ingestion.parsers import register

SUPPORTED_SESSION_EXTENSIONS = {".jsonl"}
MAX_EXCHANGE_CHARS = 600


@register("agent_session")
def parse_agent_sessions(
    source_dir: Path,
    project: str,
    source_tool: str,
    privacy_scope: str,
) -> list[IngestionRecord]:
    """Walk a directory for JSONL session logs and produce exchange records."""
    records: list[IngestionRecord] = []
    resolved = source_dir.resolve()
    if not resolved.exists():
        return records

    if resolved.is_file():
        candidates = [resolved]
    else:
        candidates = sorted(
            p for p in resolved.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_SESSION_EXTENSIONS
        )

    for file_path in candidates:
        file_records = _parse_one_session(file_path, project, source_tool, privacy_scope, resolved)
        records.extend(file_records)
    return records


def _parse_one_session(
    file_path: Path,
    project: str,
    source_tool: str,
    privacy_scope: str,
    base_dir: Path,
) -> list[IngestionRecord]:
    mtime = _file_mtime_iso(file_path)
    lines: list[dict[str, object]] = []
    try:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    for line_no, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            lines.append(obj)

    if not lines:
        return []

    session_id = _extract_session_id(lines, file_path.stem)
    created_at = _extract_created_at(lines) or mtime

    exchanges = _build_exchanges(lines)
    compactions = _extract_compactions(lines)

    if not exchanges and not compactions:
        return []

    records = []
    for idx, exchange_text in enumerate(exchanges):
        records.append(IngestionRecord(
            project=project,
            source_type="agent_session",
            source_path=str(file_path.resolve()),
            source_tool=source_tool,
            text=exchange_text,
            session_id=session_id,
            created_at=created_at,
            last_modified=mtime,
            privacy_scope=privacy_scope,
            metadata={"session_line_count": len(lines), "exchange_index": idx},
        ))

    if compactions:
        digest = "## Compaction Summaries\n\n" + "\n\n".join(compactions)
        records.append(IngestionRecord(
            project=project,
            source_type="agent_session",
            source_path=str(file_path.resolve()),
            source_tool=source_tool,
            text=digest,
            session_id=session_id,
            created_at=created_at,
            last_modified=mtime,
            privacy_scope=privacy_scope,
            metadata={"session_line_count": len(lines), "exchange_index": len(exchanges), "is_compaction": True},
        ))

    return records


def _build_exchanges(lines: list[dict[str, object]]) -> list[str]:
    """Build per-exchange text chunks from session lines.

    Groups: one user query + all subsequent assistant text until the next
    non-tool user message.
    """
    exchanges: list[str] = []
    current_user: str = ""
    current_assistant_parts: list[str] = []

    def flush() -> None:
        if not current_user:
            return
        assistant_text = " ".join(current_assistant_parts).strip()
        user_part = current_user[:MAX_EXCHANGE_CHARS]
        remaining = MAX_EXCHANGE_CHARS - len(user_part)
        if assistant_text and remaining > 20:
            assistant_part = assistant_text[:remaining]
            exchange = f"[User]: {user_part}\n[Assistant]: {assistant_part}"
        else:
            exchange = f"[User]: {user_part}"
        exchanges.append(exchange)

    for obj in _iter_message_objects(lines):
        role = _resolve_role(obj)
        if role == "user":
            text = _extract_text(obj)
            if not text or _is_noise_user_text(text):
                continue
            flush()
            current_user = text.strip()
            current_assistant_parts = []
        elif role == "assistant":
            text = _extract_text(obj)
            if text:
                current_assistant_parts.append(text.strip())

    flush()
    return exchanges


def _extract_compactions(lines: list[dict[str, object]]) -> list[str]:
    """Extract compaction / summary entries from all known formats."""
    summaries: list[str] = []
    for obj in lines:
        msg_type = str(obj.get("type", "")).lower()
        payload = obj.get("payload")

        # Standard compaction entries
        if isinstance(payload, dict):
            payload_type = str(payload.get("type", "")).lower()
            if msg_type in ("compacted", "compaction", "summary") or payload_type in (
                "compacted",
                "compaction",
                "summary",
            ):
                text = _extract_text_from_content(payload.get("summary"))
                if not text:
                    text = _extract_text_from_content(payload.get("content"))
                if text:
                    summaries.append(text)
                    continue

        if msg_type in ("compacted", "compaction", "summary"):
            message = obj.get("message")
            if isinstance(message, dict):
                text = _extract_text_from_content(message.get("content"))
                if text:
                    summaries.append(text)
            summary = obj.get("summary")
            if isinstance(summary, str) and summary.strip():
                summaries.append(summary.strip())

        # Codex turn_context.summary
        if msg_type == "turn_context" and isinstance(payload, dict):
            summary = payload.get("summary")
            if isinstance(summary, str) and summary.strip():
                summaries.append(summary.strip())

        # Claude Code compaction injection: user message starting with
        # "This session is being continued from a previous conversation..."
        if msg_type == "user":
            message = obj.get("message", {})
            content = ""
            if isinstance(message, dict):
                content = message.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        content = block.get("text", "")
                        break
            if isinstance(content, str) and content.strip().startswith(
                "This session is being continued"
            ):
                summaries.append(content.strip())

    return summaries


def _iter_message_objects(lines: list[dict[str, object]]):
    """Yield normalized message-like objects from known session schemas."""
    for obj in lines:
        msg_type = str(obj.get("type", "")).lower()
        payload = obj.get("payload")
        if msg_type == "response_item" and isinstance(payload, dict):
            if str(payload.get("type", "")).lower() == "message":
                yield payload
            continue
        if msg_type == "event_msg" and isinstance(payload, dict):
            pt = str(payload.get("type", "")).lower()
            if pt == "user_message":
                text = payload.get("message")
                if isinstance(text, str):
                    yield {"role": "user", "content": text}
            elif pt == "agent_message":
                # Codex agent text response
                text = payload.get("message")
                if isinstance(text, str):
                    yield {"role": "assistant", "content": text}
            continue
        yield obj


def _resolve_role(obj: dict[str, object]) -> str:
    """Determine the role/type of a session line."""
    # Codex-style: type field at top level
    msg_type = str(obj.get("type", "")).lower()
    if msg_type in ("user", "assistant", "system"):
        return msg_type

    # Claude API style: role in nested message
    message = obj.get("message")
    if isinstance(message, dict):
        role = str(message.get("role", "")).lower()
        if role in ("user", "assistant", "system"):
            return role
        # Some formats have type inside message
        inner_type = str(message.get("type", "")).lower()
        if inner_type in ("user", "assistant", "system"):
            return inner_type

    # Direct role field (OpenAI-style flattened)
    role = str(obj.get("role", "")).lower()
    if role in ("user", "assistant", "system"):
        return role

    return ""


def _extract_text(obj: dict[str, object]) -> str:
    """Extract human-readable text from a session line, skipping tool calls."""

    # First try nested message block (Codex-style)
    message = obj.get("message")
    if isinstance(message, dict):
        text = _extract_text_from_content(message.get("content"))
        if text:
            return text
        # Simple string content
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    # Direct content field (Claude API style)
    text = _extract_text_from_content(obj.get("content"))
    if text:
        return text

    # Top-level text/message string
    for key in ("text", "message"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            # Skip system/developer instructions
            if str(obj.get("type", "")).lower() == "system":
                return ""
            if str(obj.get("role", "")).lower() == "system":
                return ""
            return value.strip()

    return ""


def _extract_text_from_content(content: object) -> str:
    """Extract text from a content field that may be a string or list of blocks.

    Skips tool_use / tool_call blocks - only returns human-readable text.
    """
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", "")).lower()
            # Skip tool use/call blocks
            if block_type in ("tool_use", "tool_call", "tool_result", "tool"):
                continue
            block_text = block.get("text")
            if block_text is None:
                block_text = block.get("content")
            if isinstance(block_text, str) and block_text.strip():
                text_parts.append(block_text.strip())
        return "\n\n".join(text_parts)

    return ""


def _find_model(lines: list[dict[str, object]]) -> str:
    """Try to find model info from session metadata."""
    for obj in lines:
        payload = obj.get("payload")
        if isinstance(payload, dict):
            model = payload.get("model")
            if isinstance(model, str) and model.strip():
                return model.strip()
        if str(obj.get("type", "")).lower() != "system":
            continue
        message = obj.get("message")
        if isinstance(message, dict):
            model = message.get("model")
            if isinstance(model, str) and model.strip():
                return model.strip()
        model = obj.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    return ""


def _extract_session_id(lines: list[dict[str, object]], fallback: str) -> str | None:
    """Try to extract a session/conversation ID from metadata."""
    for obj in lines:
        payload = obj.get("payload")
        if isinstance(payload, dict):
            for key in ("session_id", "conversation_id", "id"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("session_id", "conversation_id", "id"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    # Use filename stem as fallback identifier
    if fallback:
        return f"session:{fallback}"
    return None


def _extract_created_at(lines: list[dict[str, object]]) -> str | None:
    """Try to extract a timestamp from session metadata."""
    for obj in lines:
        payload = obj.get("payload")
        if isinstance(payload, dict):
            for key in ("created_at", "timestamp", "created"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            ts = payload.get("timestamp")
            if isinstance(ts, (int, float)):
                iso = _numeric_timestamp_to_iso(ts)
                if iso is not None:
                    return iso
        for key in ("created_at", "timestamp", "created"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        # Some formats use numeric timestamps
        ts = obj.get("timestamp")
        if isinstance(ts, (int, float)):
            iso = _numeric_timestamp_to_iso(ts)
            if iso is not None:
                return iso
    return None


def _numeric_timestamp_to_iso(value: int | float) -> str | None:
    """Convert seconds or milliseconds since epoch to ISO, ignoring bad values."""
    ts = float(value)
    if abs(ts) >= 1_000_000_000_000:
        ts = ts / 1000.0
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _file_mtime_iso(path: Path) -> str:
    try:
        stat = path.stat()
        return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return datetime.now(timezone.utc).isoformat()


_NOISE_PREFIXES = (
    "<environment_context>",
    "<turn_aborted>",
    "<INSTRUCTIONS>",
    "<instructions>",
    "<system>",
    "<tool_result>",
    "This session is being continued",  # Claude Code compaction injection — handled separately
)
_MIN_EXCHANGE_TEXT_LEN = 3


def _is_noise_user_text(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < _MIN_EXCHANGE_TEXT_LEN:
        return True
    return any(stripped.startswith(prefix) for prefix in _NOISE_PREFIXES)

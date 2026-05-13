"""Deterministic text chunking with lightweight Markdown heading tracking."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class TextChunk:
    """A source text slice ready for indexing."""

    index: int
    content: str
    heading_path: str | None
    content_hash: str
    token_count: int


def content_hash(content: str) -> str:
    """Return a stable SHA-256 hash for stored content."""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def estimate_tokens(content: str) -> int:
    """Cheap deterministic token estimate suitable for local metadata."""

    return len(re.findall(r"\S+", content))


def chunk_text(text: str, *, max_chars: int = 1200) -> list[TextChunk]:
    """Split text into stable chunks without external tokenizers."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    chunks: list[TextChunk] = []
    heading_stack: list[tuple[int, str]] = []
    current_lines: list[str] = []
    current_heading: str | None = None

    def flush() -> None:
        nonlocal current_lines, current_heading
        body = "\n".join(current_lines).strip()
        if not body:
            current_lines = []
            return
        chunks.append(
            TextChunk(
                index=len(chunks),
                content=body,
                heading_path=current_heading,
                content_hash=content_hash(body),
                token_count=estimate_tokens(body),
            )
        )
        current_lines = []

    for line in normalized.split("\n"):
        heading_match = HEADING_RE.match(line)
        if heading_match:
            if current_lines:
                flush()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_stack = [(lvl, txt) for lvl, txt in heading_stack if lvl < level]
            heading_stack.append((level, title))
            current_heading = " > ".join(txt for _, txt in heading_stack)

        if current_lines and sum(len(item) + 1 for item in current_lines) + len(line) > max_chars:
            flush()

        current_lines.append(line)

    flush()
    return chunks

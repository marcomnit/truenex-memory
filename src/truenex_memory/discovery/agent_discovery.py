"""Agent discovery: scan local Codex/Claude roots for projects, docs, servers.

Does NOT scan the whole PC. Only looks under agent client directories.
Does NOT mutate the memory database. Discovery only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import re

# ── data model ────────────────────────────────────────────────────────

@dataclass
class AgentRoot:
    """A discovered agent client directory."""
    label: str          # e.g. "codex-sessions", "claude-projects"
    path: Path
    exists: bool
    file_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class CandidateProject:
    """A candidate project path discovered from agent data."""
    root: str
    discovered_from: list[str] = field(default_factory=list)  # agent root labels
    confidence: float = 0.0


@dataclass
class CandidateDocument:
    """A candidate document path discovered from agent data."""
    path: str
    discovered_from: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class ServerAlias:
    """An SSH/server alias discovered from agent data."""
    alias: str
    source: str = "agent-history"       # agent root label(s), comma-separated after merge
    confidence: float = 0.0


@dataclass
class DiscoveryReport:
    """Full discovery report with sections and counts."""
    agent_roots: list[AgentRoot] = field(default_factory=list)
    projects: list[CandidateProject] = field(default_factory=list)
    documents: list[CandidateDocument] = field(default_factory=list)
    servers: list[ServerAlias] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def project_count(self) -> int:
        return len(self.projects)

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def server_count(self) -> int:
        return len(self.servers)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_roots": [
                {
                    "label": r.label,
                    "path": str(r.path),
                    "exists": r.exists,
                    "file_count": r.file_count,
                    "warnings": r.warnings,
                }
                for r in self.agent_roots
            ],
            "projects": [
                {
                    "root": p.root,
                    "discovered_from": p.discovered_from,
                    "evidence_count": len(p.discovered_from),
                    "confidence": p.confidence,
                }
                for p in self.projects
            ],
            "documents": [
                {
                    "path": d.path,
                    "discovered_from": d.discovered_from,
                    "evidence_count": len(d.discovered_from),
                    "confidence": d.confidence,
                }
                for d in self.documents
            ],
            "servers": [
                {
                    "alias": s.alias,
                    "source": s.source,
                    "evidence_count": len(_split_sources(s.source)),
                    "confidence": s.confidence,
                }
                for s in self.servers
            ],
            "warnings": self.warnings,
        }


# ── agent root layout ─────────────────────────────────────────────────

DEFAULT_AGENT_MANIFEST: dict[str, object] = {
    "version": 1,
    "agents": [
        {
            "name": "codex",
            "dir": ".codex",
            "roots": [
                {"label": "sessions", "subdir": "sessions"},
                {"label": "history", "subdir": "history.jsonl"},
                {"label": "memories", "subdir": "memories"},
            ],
        },
        {
            "name": "claude",
            "dir": ".claude",
            "roots": [
                {"label": "projects", "subdir": "projects"},
                {"label": "commands", "subdir": "commands"},
                {"label": "history", "subdir": "history.jsonl"},
                {"label": "skills", "subdir": "skills"},
            ],
        },
        {
            "name": "kimi",
            "dir": ".kimi",
            "roots": [
                {"label": "sessions", "subdir": "sessions"},
                {"label": "plans", "subdir": "plans"},
                {"label": "skills", "subdir": "skills"},
            ],
        },
        {
            "name": "cursor",
            "dir": ".cursor",
            "roots": [
                {"label": "projects", "subdir": "projects"},
            ],
        },
        {
            "name": "openclaw",
            "dir": ".kimi_openclaw",
            "roots": [
                {"label": "workspace", "subdir": "workspace"},
                {"label": "agents", "subdir": "agents"},
            ],
        },
        {
            "name": "aider",
            "dir": ".aider",
            "roots": [
                {"label": "caches", "subdir": "caches"},
            ],
        },
        {
            "name": "antigravity",
            "dir": ".antigravity",
            "roots": [
                {"label": "extensions", "subdir": "extensions"},
            ],
        },
        {
            "name": "gemini",
            "dir": ".gemini",
            "roots": [
                {"label": "antigravity", "subdir": "antigravity"},
            ],
        },
    ],
}


def _agent_manifest_path(home: Path | None = None) -> Path:
    """Return the path to the agent manifest file."""
    return (home or Path.home()) / ".truenex-memory" / "agent_manifest.json"


def load_agent_manifest(home: Path | None = None) -> dict[str, object]:
    """Load the agent manifest from disk, creating it with defaults if missing."""
    path = _agent_manifest_path(home)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_AGENT_MANIFEST, indent=2), encoding="utf-8")
    return dict(DEFAULT_AGENT_MANIFEST)


def save_agent_manifest(manifest: dict[str, object]) -> None:
    """Save the agent manifest to disk."""
    path = _agent_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def add_agent_to_manifest(
    name: str, relative_dir: str, subdir: str, *, label: str | None = None
) -> None:
    """Add a new agent to the manifest."""
    manifest = load_agent_manifest()
    agents: list[dict[str, object]] = manifest.setdefault("agents", [])
    agents = [a for a in agents if a.get("name") != name]
    agents.append(
        {
            "name": name,
            "dir": relative_dir,
            "roots": [{"label": label or subdir, "subdir": subdir}],
        }
    )
    manifest["agents"] = agents
    save_agent_manifest(manifest)


def remove_agent_from_manifest(name: str) -> None:
    """Remove an agent from the manifest by name."""
    manifest = load_agent_manifest()
    agents: list[dict[str, object]] = manifest.setdefault("agents", [])
    manifest["agents"] = [a for a in agents if a.get("name") != name]
    save_agent_manifest(manifest)

# Subdirectories that are NEVER useful for memory extraction and should be
# skipped during heuristic discovery.
EXCLUDED_SUBDIRS = frozenset({
    "logs", "telemetry", "credentials", "cache", "caches", "sandbox",
    "bin", "debug", "daemon", "tmp", "temp", ".tmp", "runtimes",
    "ai-tracking", "snapshots", "installs", "analytics", "config-backups",
    "cron", "plugins", "backups", "exports", "node_modules", "__pycache__",
    ".venv", "venv", "env", ".git", ".svn", ".hg", ".tox", "dist", "build",
    "target", ".idea", ".vscode", "coverage", "htmlcov",
})

# Subdirectory names that indicate a directory likely belongs to an agent.
_AGENT_SUBDIR_SIGNALS = frozenset({
    "sessions", "projects", "skills", "plans", "commands", "workspace",
    "agents", "memories", "history.jsonl", "input-history", "extensions",
})


def _discovery_prefs_path(home: Path | None = None) -> Path:
    """Return the path to the discovery preferences file."""
    return (home or Path.home()) / ".truenex-memory" / "discovery_prefs.json"


def _load_discovery_prefs(home: Path | None = None) -> dict:
    """Load discovery preferences from disk."""
    path = _discovery_prefs_path(home)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "included_heuristic": [],
        "custom_roots": [],
        "excluded_defaults": [],
    }


def _save_discovery_prefs(prefs: dict) -> None:
    """Save discovery preferences to disk."""
    path = _discovery_prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")


def get_effective_agent_roots(home: Path | None = None) -> list[tuple[str, str, str]]:
    """Return the full list of agent roots to scan.

    Combines manifest roots, user-discovered roots, and custom roots,
    minus any the user explicitly excluded.
    """
    prefs = _load_discovery_prefs(home)
    manifest = load_agent_manifest(home)

    roots: list[tuple[str, str, str]] = []
    for agent in manifest.get("agents", []):
        rel_dir = agent.get("dir", "")
        for root in agent.get("roots", []):
            label = f"{agent['name']}-{root['label']}"
            roots.append((label, rel_dir, root["subdir"]))

    # Add heuristic roots the user has approved
    for item in prefs.get("included_heuristic", []):
        label = item.get("label", "")
        root = item.get("root", "")
        subdir = item.get("subdir", "")
        if label and root and subdir:
            roots.append((label, root, subdir))

    # Add custom roots
    for item in prefs.get("custom_roots", []):
        label = item.get("label", "")
        root = item.get("root", "")
        subdir = item.get("subdir", "")
        if label and root and subdir:
            roots.append((label, root, subdir))

    # Remove excluded defaults
    excluded = {tuple(e) for e in prefs.get("excluded_defaults", [])}
    roots = [r for r in roots if r not in excluded]

    return roots


def heuristic_discovery(home: Path) -> list[tuple[str, str, str]]:
    """Scan *home* for hidden directories that look like agent roots.

    Returns a list of (label, relative_dir, sub_dir) tuples for directories
    that contain agent-like subdirectories but are NOT already in the manifest.
    """
    manifest = load_agent_manifest()
    known: set[tuple[str, str]] = set()
    for agent in manifest.get("agents", []):
        rel_dir = agent.get("dir", "")
        for root in agent.get("roots", []):
            known.add((rel_dir, root["subdir"]))

    # Also respect user prefs
    prefs = _load_discovery_prefs()
    for item in prefs.get("included_heuristic", []):
        root = item.get("root", "")
        subdir = item.get("subdir", "")
        if root and subdir:
            known.add((root, subdir))
    for item in prefs.get("custom_roots", []):
        root = item.get("root", "")
        subdir = item.get("subdir", "")
        if root and subdir:
            known.add((root, subdir))

    found: list[tuple[str, str, str]] = []

    if not home.exists() or not home.is_dir():
        return found

    for entry in home.iterdir():
        if not entry.is_dir() or not entry.name.startswith("."):
            continue

        rel_dir = entry.name
        try:
            sub_entries = [e for e in entry.iterdir() if e.name not in EXCLUDED_SUBDIRS]
        except (OSError, PermissionError):
            continue

        for sub in sub_entries:
            sub_name = sub.name
            if sub_name in EXCLUDED_SUBDIRS:
                continue
            if sub_name in _AGENT_SUBDIR_SIGNALS:
                if (rel_dir, sub_name) not in known:
                    label = f"{rel_dir.lstrip('.').replace('_', '-')}-{sub_name}"
                    found.append((label, rel_dir, sub_name))

    # Deduplicate while preserving order
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str, str]] = []
    for item in found:
        key = (item[1], item[2])
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return deduped

DOC_EXTENSIONS = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".toml"})
MAX_FILE_READ_CHARS = 200_000
MAX_TEXT_CHARS = 20_000
MAX_TEXTS_PER_JSONL_FILE = 500
MAX_PATH_MATCHES_PER_TEXT = 250
MAX_DOC_MATCHES_PER_TEXT = 250
RELEVANT_JSON_KEYS = frozenset(
    {
        "content",
        "cwd",
        "message",
        "path",
        "root",
        "source_path",
        "summary",
        "text",
    }
)

# Regex patterns
_RE_ABS_PATH_WIN = re.compile(r'[A-Za-z]:[\\/][^\s"\'<>|*?]+')
_RE_ABS_PATH_UNIX = re.compile(r'(?:^|\s)/(?:[^\s"\'*?|]+/)+[^\s"\'*?|]*')
_RE_SSH_ALIAS = re.compile(r'\bssh\s+(?:root@)?([\w][\w.-]*)\b', re.IGNORECASE)
_RE_SSH_ROOT_AT = re.compile(r'\bssh\s+root@(\S+)\b', re.IGNORECASE)
_RE_SSH_USER_AT = re.compile(r'\bssh\s+(\w+)@(\S+)\b', re.IGNORECASE)
_DOC_EXTENSION_HINTS = tuple(sorted(DOC_EXTENSIONS))
_DOC_PATH_RE = re.compile(
    r'[^\s"\'<>|]*\.(?:md|txt|json|ya?ml|toml)',
    re.IGNORECASE,
)
_UNIX_PROJECT_PREFIXES = (
    "/home/",
    "/mnt/",
    "/opt/",
    "/root/",
    "/srv/",
    "/users/",
    "/var/www/",
    "/workspace/",
)
_PROJECT_PATH_REJECT_CHARS = frozenset("{}<>[]`")
EXCLUDED_PROJECT_SEGMENTS = frozenset(
    {
        ".agents",
        ".claude",
        ".codex",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
        "site-packages",
        "venv",
    }
)
WINDOWS_PROJECT_ANCHORS = frozenset({"projectpy", "software", "sofware"})
WINDOWS_USER_PROJECT_ANCHORS = frozenset({"documents", "documenti", "projects", "repos"})
WINDOWS_PROJECT_STOP_SEGMENTS = frozenset({"docs", "src", "tests", "memory", "diary"})
WINDOWS_NON_PROJECT_SEGMENTS = frozenset(
    {
        ".cursor",
        ".ssh",
        "appdata",
        "codex_tmp",
        "downloads",
        "system32",
        "tmp",
        "windows",
    }
)
CANONICAL_RELATIVE_DOCS = frozenset({"agents.md", "claude.md", "readme.md"})
EXCLUDED_DOCUMENT_PATH_FRAGMENTS = (
    ".agent/",
    ".agents/skills/",
    ".claude/shell-snapshots/",
    ".codex/skills/",
    "/.agent/",
    "/.agents/skills/",
    "/.claude/shell-snapshots/",
    "/.codex/skills/",
)

# Common English words that can follow "SSH" in prose but aren't aliases
_SSH_NOISE_WORDS = frozenset({
    "to", "the", "a", "an", "is", "it", "of", "in", "on", "at", "and",
    "or", "not", "no", "with", "for", "from", "by", "as", "be", "we",
    "key", "agent", "add", "config", "user", "root", "host", "server",
    "connection", "using", "via", "into", "references",
    "double-quoted", "read-only",
})


# ── discovery ─────────────────────────────────────────────────────────

def _is_known_agent_text_preamble(text: str) -> bool:
    """Skip internal agent instructions that aren't real user paths."""
    return any(
        text.startswith(prefix)
        for prefix in (
            "<environment",
            "<system",
            "<developer",
            "<instructions",
            "<agent",
            "<turn_aborted",
        )
    )


def _extract_strings(obj: object) -> list[str]:
    """Recursively extract all string values from a parsed JSON object."""
    strings: list[str] = []
    if isinstance(obj, str):
        strings.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            strings.extend(_extract_strings(v))
    elif isinstance(obj, list):
        for item in obj:
            strings.extend(_extract_strings(item))
    return strings


def _extract_relevant_strings(obj: object, *, parent_key: str = "") -> list[str]:
    """Extract only likely human/project strings from a parsed JSON object."""
    strings: list[str] = []
    if isinstance(obj, str):
        if parent_key in RELEVANT_JSON_KEYS:
            strings.append(_trim_text(obj))
    elif isinstance(obj, dict):
        for key, value in obj.items():
            lower_key = str(key).lower()
            if lower_key in ("tool_call", "tool_calls", "tool_result", "tool_use", "input"):
                continue
            if lower_key in RELEVANT_JSON_KEYS:
                strings.extend(_extract_relevant_strings(value, parent_key=lower_key))
            elif isinstance(value, (dict, list)):
                strings.extend(_extract_relevant_strings(value, parent_key=lower_key))
    elif isinstance(obj, list):
        for item in obj:
            strings.extend(_extract_relevant_strings(item, parent_key=parent_key))
    return [text for text in strings if text]


def _trim_text(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= MAX_TEXT_CHARS:
        return stripped
    return stripped[:MAX_TEXT_CHARS]


def _bounded_read_text(file_path: Path) -> tuple[str, bool]:
    """Read at most *MAX_FILE_READ_CHARS* from *file_path*.

    Returns ``(text, was_truncated)``.  *was_truncated* is ``True`` when the
    file contains more data beyond the read limit.
    """
    text_parts: list[str] = []
    total_chars = 0
    truncated = False
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                text_parts.append(chunk)
                total_chars += len(chunk)
                if total_chars >= MAX_FILE_READ_CHARS:
                    extra = f.read(1)
                    truncated = bool(extra)
                    break
        full_text = "".join(text_parts)
        if truncated:
            full_text = full_text[:MAX_FILE_READ_CHARS]
        return (full_text, truncated)
    except OSError:
        return ("", False)


def _extract_text_from_jsonl(file_path: Path) -> tuple[list[str], bool]:
    """Stream and parse a JSONL file line-by-line.

    Returns ``(texts, was_truncated)``.  Never reads the whole file into
    memory at once.  *was_truncated* is ``True`` when the file had more
    data after *MAX_TEXTS_PER_JSONL_FILE* texts were collected.
    """
    texts: list[str] = []
    truncated = False
    try:
        f = open(file_path, encoding="utf-8", errors="replace")
    except OSError:
        return (texts, False)

    with f:
        for line in f:
            if len(texts) >= MAX_TEXTS_PER_JSONL_FILE:
                try:
                    next(f)
                    truncated = True
                except StopIteration:
                    pass
                break

            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            # Skip developer/system instructions in older flattened schemas.
            role = str(obj.get("role", "")).lower()
            msg_type = str(obj.get("type", "")).lower()
            if role == "developer" or role == "system":
                continue
            if msg_type == "system":
                continue

            payload = obj.get("payload")
            if isinstance(payload, dict):
                payload_role = str(payload.get("role", "")).lower()
                if payload_role in ("developer", "system"):
                    continue

                # Extract cwd from session_meta payloads (Codex-style).
                cwd = payload.get("cwd")
                if isinstance(cwd, str) and cwd.strip():
                    texts.append(_trim_text(cwd))

                if msg_type == "response_item" and str(payload.get("type", "")).lower() == "message":
                    content_text = _extract_message_content_text(payload.get("content"))
                    if content_text:
                        texts.append(content_text)
                    continue

                if msg_type == "event_msg" and str(payload.get("type", "")).lower() == "user_message":
                    message = payload.get("message")
                    if isinstance(message, str) and message.strip():
                        texts.append(_trim_text(message))
                    continue

            message = obj.get("message")
            if isinstance(message, dict):
                message_role = str(message.get("role", "")).lower()
                if message_role in ("developer", "system"):
                    continue
                content_text = _extract_message_content_text(message.get("content"))
                if content_text:
                    texts.append(content_text)
                    continue

            # Fallback for simple JSONL history entries. This is intentionally
            # selective so tool payloads are not treated as prose.
            texts.extend(_extract_relevant_strings(obj))
            if len(texts) >= MAX_TEXTS_PER_JSONL_FILE:
                texts = texts[:MAX_TEXTS_PER_JSONL_FILE]
                try:
                    next(f)
                    truncated = True
                except StopIteration:
                    pass
                break

    return (texts, truncated)


def _extract_message_content_text(content: object) -> str:
    """Extract human prose from a message content field."""
    if isinstance(content, str):
        return _trim_text(content)
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "")).lower()
        if block_type in ("tool_use", "tool_call", "tool_result", "tool"):
            continue
        text = block.get("text")
        if text is None:
            text = block.get("content")
        if isinstance(text, str) and text.strip():
            parts.append(_trim_text(text))
    return _trim_text("\n\n".join(parts))


def _find_paths_in_text(text: str) -> list[str]:
    """Find candidate absolute paths in a text string."""
    found: list[str] = []
    for match in _RE_ABS_PATH_WIN.finditer(text):
        p = _clean_candidate_path(match.group(0))
        if len(p) >= 3 and any(c in p for c in "/\\"):
            found.append(p)
            if len(found) >= MAX_PATH_MATCHES_PER_TEXT:
                return found
    for match in _RE_ABS_PATH_UNIX.finditer(text):
        p = _clean_candidate_path(match.group(0))
        if len(p) >= 3 and p.count("/") >= 1:
            found.append(p)
            if len(found) >= MAX_PATH_MATCHES_PER_TEXT:
                return found
    return found


def _clean_candidate_path(path: str) -> str:
    return path.strip().strip("`[](){}<>").rstrip(".,;:!?\"'")


def _looks_like_project_root_path(path: str) -> bool:
    """Return whether a non-existing path looks like a project/root path.

    Discovery sees a lot of API routes and code snippets in agent sessions. They
    are useful context, but they are not project source roots.
    """
    cleaned = path.strip()
    if any(char in cleaned for char in _PROJECT_PATH_REJECT_CHARS):
        return False
    if Path(cleaned).suffix:
        return False
    normalized = cleaned.replace("\\", "/")
    lower = normalized.lower()
    if re.match(r"^[a-z]:/", lower):
        return len(Path(cleaned).parts) >= 3
    parts = [part for part in lower.split("/") if part]
    if lower.startswith("/home/") or lower.startswith("/users/"):
        return len(parts) >= 3
    if lower.startswith("/opt/") or lower.startswith("/srv/") or lower.startswith("/workspace/"):
        return len(parts) >= 2
    if lower.startswith("/var/www/"):
        return len(parts) >= 3
    if lower.startswith("/mnt/"):
        return len(parts) >= 4
    return False


def _project_root_from_path(path: str) -> str | None:
    """Infer a useful project/root directory from a discovered path."""
    cleaned = _clean_candidate_path(path)
    if not cleaned or any(char in cleaned for char in _PROJECT_PATH_REJECT_CHARS):
        return None
    if len(re.findall(r"[A-Za-z]:", cleaned)) > 1:
        return None

    normalized = cleaned.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    lowered_parts = [part.lower() for part in parts]
    if lowered_parts and lowered_parts[0] in ("a:", "b:"):
        return None
    if any(part in {".agents", ".claude", ".codex"} for part in lowered_parts):
        return None
    if any(part in WINDOWS_NON_PROJECT_SEGMENTS for part in lowered_parts):
        return None
    if any(part in EXCLUDED_PROJECT_SEGMENTS for part in lowered_parts):
        # Keep the parent project if the excluded segment appears under it.
        first_excluded = next(
            index for index, part in enumerate(lowered_parts)
            if part in EXCLUDED_PROJECT_SEGMENTS
        )
        parts = parts[:first_excluded]
        lowered_parts = lowered_parts[:first_excluded]
        if not parts:
            return None

    if re.match(r"^[a-z]:", normalized.lower()):
        if lowered_parts[0] not in ("c:", "d:"):
            return None
        for index, part in enumerate(lowered_parts):
            if part in WINDOWS_PROJECT_ANCHORS and index + 1 < len(parts):
                return _join_windows_parts(parts[: index + 2])
            if part in WINDOWS_USER_PROJECT_ANCHORS and index + 1 < len(parts):
                return _join_windows_parts(parts[: index + 2])
        for index, part in enumerate(lowered_parts):
            if part in WINDOWS_PROJECT_STOP_SEGMENTS and index >= 1:
                return _join_windows_parts(parts[:index])
        return None

    lower = normalized.lower()
    if lower.startswith("/home/") or lower.startswith("/users/"):
        if len(parts) >= 3:
            return "/" + "/".join(parts[:3])
    if lower.startswith("/opt/") or lower.startswith("/srv/") or lower.startswith("/workspace/"):
        if len(parts) >= 2:
            return "/" + "/".join(parts[:2])
    if lower.startswith("/var/www/"):
        if len(parts) >= 3:
            return "/" + "/".join(parts[:3])
    if lower.startswith("/mnt/") and len(parts) >= 4:
        return "/" + "/".join(parts[:4])
    return cleaned if _looks_like_project_root_path(cleaned) else None


def _join_windows_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    first = parts[0]
    if first.endswith(":"):
        return first + "\\" + "\\".join(parts[1:])
    return "\\".join(parts)


def _find_ssh_aliases(text: str) -> list[str]:
    """Find SSH aliases and hosts in a text string."""
    aliases: list[str] = []

    # ssh alias or ssh host
    for match in _RE_SSH_ALIAS.finditer(text):
        alias = _clean_server_alias(match.group(1))
        if _looks_like_server_alias(alias):
            aliases.append(alias)

    # ssh root@host
    for match in _RE_SSH_ROOT_AT.finditer(text):
        host = _clean_server_alias(match.group(1))
        if host and _looks_like_server_alias(host) and host not in aliases:
            aliases.append(host)

    # ssh user@host
    for match in _RE_SSH_USER_AT.finditer(text):
        host = _clean_server_alias(match.group(2))
        if host and _looks_like_server_alias(host) and host not in aliases:
            aliases.append(host)

    return aliases


def _clean_server_alias(alias: str) -> str:
    cleaned = alias.replace("\\n", "").replace("\\r", "")
    return cleaned.strip().strip("`'\".,;:!?()[]{}<>")


def _looks_like_server_alias(alias: str) -> bool:
    cleaned = _clean_server_alias(alias)
    lower = cleaned.lower()
    if not cleaned or lower in _SSH_NOISE_WORDS:
        return False
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", cleaned):
        return True
    if "." in cleaned or "-" in cleaned:
        return True
    return False


def _find_doc_paths(text: str) -> list[str]:
    """Find candidate document file paths in a text string.

    Looks for paths ending in known doc extensions.
    """
    lower_text = text.lower()
    if not any(ext in lower_text for ext in _DOC_EXTENSION_HINTS):
        return []

    docs: list[str] = []
    for match in _DOC_PATH_RE.finditer(text):
        candidate = _clean_doc_candidate(match.group(0))
        if candidate is not None:
            docs.append(candidate)
            if len(docs) >= MAX_DOC_MATCHES_PER_TEXT:
                return docs
    return docs


def _clean_doc_candidate(candidate: str) -> str | None:
    cleaned = candidate.strip().strip("`[](){}<>").rstrip(".,;:!?\"'")
    if not cleaned:
        return None
    lower = cleaned.lower().replace("\\", "/")
    if lower.startswith(("http://", "https://")):
        return None
    if _is_excluded_document_path(lower):
        return None
    if any(char in cleaned for char in "<>[]()`{}"):
        return None
    if re.match(r"^[a-z]:/", lower) or lower.startswith("/"):
        return cleaned
    if "/" in lower:
        return cleaned
    if lower in CANONICAL_RELATIVE_DOCS:
        return cleaned
    return None


def _is_excluded_document_path(normalized_lower: str) -> bool:
    """Filter agent/tool internals that are not user project documents."""
    return any(fragment in normalized_lower for fragment in EXCLUDED_DOCUMENT_PATH_FRAGMENTS)


def _scan_agent_root(
    label: str, home: Path, relative_dir: str, sub_dir: str
) -> tuple[AgentRoot, list[str]]:
    """Scan one agent root directory and return the AgentRoot and extracted texts."""
    path = home / relative_dir / sub_dir
    warnings: list[str] = []
    texts: list[str] = []

    if not path.exists():
        return AgentRoot(label=label, path=path, exists=False, warnings=[]), texts

    # Gather files and count
    files: list[Path] = []
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file())
    elif path.is_file():
        files = [path]

    for file_path in files:
        try:
            suffix = file_path.suffix.lower()
            if suffix == ".jsonl":
                file_texts, was_truncated = _extract_text_from_jsonl(file_path)
                texts.extend(file_texts)
                if was_truncated:
                    warnings.append(
                        f"{label}/{file_path.name}: truncated at "
                        f"{MAX_TEXTS_PER_JSONL_FILE} texts"
                    )
            elif suffix == ".json":
                raw, was_truncated = _bounded_read_text(file_path)
                if was_truncated:
                    warnings.append(
                        f"{label}/{file_path.name}: truncated at "
                        f"{MAX_FILE_READ_CHARS} chars"
                    )
                try:
                    obj = json.loads(raw)
                    texts.extend(_extract_relevant_strings(obj))
                except json.JSONDecodeError:
                    texts.append(_trim_text(raw))
            elif suffix in (".md", ".txt", ".yaml", ".yml", ".toml"):
                raw, was_truncated = _bounded_read_text(file_path)
                if was_truncated:
                    warnings.append(
                        f"{label}/{file_path.name}: truncated at "
                        f"{MAX_FILE_READ_CHARS} chars"
                    )
                texts.append(_trim_text(raw))
        except Exception as exc:
            warnings.append(f"{label}/{file_path.name}: {exc}")
            continue

    return (
        AgentRoot(label=label, path=path, exists=True, file_count=len(files), warnings=warnings[:10]),
        texts,
    )


def _deduplicate_projects(candidates: list[CandidateProject]) -> list[CandidateProject]:
    """Deduplicate project paths by normalizing and merging discovered_from."""
    seen: dict[str, CandidateProject] = {}
    for cand in candidates:
        norm = _normalize_discovered_path(cand.root)
        key = norm.lower()
        if key in seen:
            existing = seen[key]
            for src in cand.discovered_from:
                if src not in existing.discovered_from:
                    existing.discovered_from.append(src)
        else:
            seen[key] = CandidateProject(root=norm, discovered_from=list(cand.discovered_from))
    return list(seen.values())


def _safe_is_dir(path: Path) -> bool:
    """Return whether a path is a directory without surfacing OS access errors."""
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_exists(path: Path) -> bool:
    """Return whether a path exists without surfacing OS access errors."""
    try:
        return path.exists()
    except OSError:
        return False


def _normalize_discovered_path(raw_path: str) -> str:
    """Normalize a discovered path without mangling remote/nonexistent paths."""
    path = raw_path.strip()
    try:
        candidate = Path(path)
    except (OSError, ValueError):
        return path
    if _safe_exists(candidate):
        try:
            return str(candidate.resolve())
        except (OSError, RuntimeError):
            return str(candidate)
    return path


def _deduplicate_documents(candidates: list[CandidateDocument]) -> list[CandidateDocument]:
    """Deduplicate document paths."""
    seen: dict[str, CandidateDocument] = {}
    for cand in candidates:
        key = cand.path.lower()
        if key in seen:
            for src in cand.discovered_from:
                if src not in seen[key].discovered_from:
                    seen[key].discovered_from.append(src)
        else:
            seen[key] = CandidateDocument(path=cand.path, discovered_from=list(cand.discovered_from))
    return list(seen.values())


def _deduplicate_servers(servers: list[ServerAlias]) -> list[ServerAlias]:
    """Deduplicate server aliases, merging sources."""
    seen: dict[str, ServerAlias] = {}
    for srv in servers:
        key = srv.alias.lower()
        if key in seen:
            sources = _split_sources(seen[key].source)
            for source in _split_sources(srv.source):
                if source not in sources:
                    sources.append(source)
            seen[key].source = ",".join(sources)
        else:
            seen[key] = srv
    # Keep the original casing for display
    return list(seen.values())


# ── confidence scoring ────────────────────────────────────────────────


def _split_sources(source: str) -> list[str]:
    """Split comma-merged source labels while preserving order."""
    return [item.strip() for item in source.split(",") if item.strip()]


def _score_project(candidate: CandidateProject) -> float:
    """Compute a deterministic confidence score for a candidate project.

    Signals:
    - 1.0 per distinct agent root that discovered it (cross-validation)
    - +1.0 if the path exists on disk as a directory (verified)
    """
    confidence = float(len(candidate.discovered_from))
    if _safe_is_dir(Path(candidate.root)):
        confidence += 1.0
    return confidence


def _score_document(candidate: CandidateDocument) -> float:
    """Compute a deterministic confidence score for a candidate document.

    Signals:
    - 1.0 per distinct agent root that discovered it
    - +1.0 if the path exists on disk
    - +0.5 if the filename is a canonical doc name (AGENTS.md, CLAUDE.md, README.md)
    """
    confidence = float(len(candidate.discovered_from))
    path = Path(candidate.path)
    if _is_absolute_path_string(candidate.path) and _safe_exists(path):
        confidence += 1.0
    if path.name.lower() in CANONICAL_RELATIVE_DOCS:
        confidence += 0.5
    return confidence


def _is_absolute_path_string(path: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    return bool(re.match(r"^[a-zA-Z]:/", normalized) or normalized.startswith("/"))


def _score_server(server: ServerAlias) -> float:
    """Compute a deterministic confidence score for a server alias.

    Signals:
    - 1.0 per distinct agent root that discovered it
    - +0.5 if the alias is a fully-qualified name (contains dots)
    - +0.5 if the alias contains hyphens (common server naming pattern)
    """
    sources = _split_sources(server.source)
    confidence = float(len(sources))
    if "." in server.alias:
        confidence += 0.5
    if "-" in server.alias:
        confidence += 0.5
    return confidence


def _collect_skill_documents(skills_dir: Path, label: str) -> list[CandidateDocument]:
    """Directly emit doc files in a skills directory as document candidates.

    Unlike normal agent-root scanning (which extracts paths from file *content*),
    this promotes the skill files themselves as indexable documents.  Only
    files with a known doc extension are included; subdirectories are walked
    recursively so nested skill layouts (e.g. ``skills/truenex/SKILL.md``) work.
    """
    docs: list[CandidateDocument] = []
    if not _safe_is_dir(skills_dir):
        return docs
    try:
        for skill_file in sorted(skills_dir.rglob("*")):
            try:
                if skill_file.is_file() and skill_file.suffix.lower() in DOC_EXTENSIONS:
                    docs.append(CandidateDocument(path=str(skill_file), discovered_from=[label]))
            except OSError:
                continue
    except OSError:
        pass
    return docs


def discover_from_agents(home: Path) -> DiscoveryReport:
    """Scan agent client directories under *home* and produce a DiscoveryReport.

    Scans all configured agent roots (hardcoded + user-discovered + custom)
    but does NOT recursively traverse discovered project directories.

    Returns a DiscoveryReport with sections for agent roots, projects,
    documents, servers, and warnings.
    """
    roots: list[AgentRoot] = []
    projects: list[CandidateProject] = []
    documents: list[CandidateDocument] = []
    servers: list[ServerAlias] = []
    warnings: list[str] = []

    for label, relative_dir, sub_dir in get_effective_agent_roots(home):
        root, texts = _scan_agent_root(label, home, relative_dir, sub_dir)
        roots.append(root)
        warnings.extend(
            f"{label}: {w}" for w in root.warnings
        )

        for text in texts:
            if _is_known_agent_text_preamble(text):
                continue

            # Extract project paths
            for found_path in _find_paths_in_text(text):
                root_path = _project_root_from_path(found_path)
                if root_path is None:
                    continue
                projects.append(
                    CandidateProject(root=root_path, discovered_from=[label])
                )

            # Extract document paths
            for doc_path in _find_doc_paths(text):
                documents.append(
                    CandidateDocument(path=doc_path, discovered_from=[label])
                )

            # Extract SSH/server aliases
            for alias in _find_ssh_aliases(text):
                servers.append(ServerAlias(alias=alias, source=label))

    # Promote .claude/skills/ files directly as document candidates so they are
    # always discovered regardless of whether their paths appear in session logs.
    documents.extend(_collect_skill_documents(home / ".claude" / "skills", "claude-skills"))

    # Deduplicate, then score and rank by confidence (highest first)
    projects = _deduplicate_projects(projects)
    documents = _deduplicate_documents(documents)
    servers = _deduplicate_servers(servers)

    for p in projects:
        p.confidence = _score_project(p)
    for d in documents:
        d.confidence = _score_document(d)
    for s in servers:
        s.confidence = _score_server(s)

    projects.sort(key=lambda p: (-p.confidence, p.root.lower()))
    documents.sort(key=lambda d: (-d.confidence, d.path.lower()))
    servers.sort(key=lambda s: (-s.confidence, s.alias.lower()))

    report = DiscoveryReport(
        agent_roots=roots,
        projects=projects,
        documents=documents,
        servers=servers,
        warnings=warnings[:50],
    )

    return report


DEFAULT_DISPLAY_LIMIT = 20


def _format_header(title: str, count: int) -> str:
    return f"\n## {title} ({count})"


def _format_project_line(p: CandidateProject) -> str:
    sources = ", ".join(p.discovered_from)
    exists = " [EXISTS]" if _safe_is_dir(Path(p.root)) else ""
    return f"- {p.root}{exists} (conf={p.confidence:.1f}, from: {sources})"


def _format_document_line(d: CandidateDocument) -> str:
    sources = ", ".join(d.discovered_from)
    exists = " [EXISTS]" if _is_absolute_path_string(d.path) and _safe_exists(Path(d.path)) else ""
    return f"- {d.path}{exists} (conf={d.confidence:.1f}, from: {sources})"


def _format_server_line(s: ServerAlias) -> str:
    return f"- {s.alias} (conf={s.confidence:.1f}, from: {s.source})"


def _append_candidate_section(
    lines: list[str],
    candidates: list,
    limit: int | None,
    formatter,
) -> None:
    """Append formatted candidate lines with optional truncation note."""
    visible = candidates[:limit] if limit is not None else candidates
    for cand in visible:
        lines.append(formatter(cand))
    if limit is not None and len(candidates) > limit:
        remaining = len(candidates) - limit
        lines.append(f"  ... and {remaining} more (use --json for full list)")


def format_report(report: DiscoveryReport, limit: int | None = DEFAULT_DISPLAY_LIMIT) -> str:
    """Format a DiscoveryReport as a human-readable markdown string.

    Candidates are ordered by confidence (highest first) with alphabetical
    tie-breaking.  Each section shows at most *limit* entries with a truncation
    note when there are more.  Pass limit=None to show all entries.
    """
    lines: list[str] = ["# Agent Discovery Report"]

    # Agent roots
    lines.append(_format_header("Agent Roots", len(report.agent_roots)))
    for r in report.agent_roots:
        status = "exists" if r.exists else "NOT FOUND"
        suffix = f" ({r.file_count} files)" if r.file_count else ""
        lines.append(f"- [{status}] {r.label}: {r.path}{suffix}")
        for w in r.warnings:
            lines.append(f"  - Warning: {w}")

    # Projects (ranked by confidence, highest first)
    lines.append(_format_header("Projects", report.project_count))
    if report.projects:
        _append_candidate_section(lines, report.projects, limit, _format_project_line)
    else:
        lines.append("- (none)")

    # Documents (ranked by confidence, highest first)
    lines.append(_format_header("Documents", report.document_count))
    if report.documents:
        _append_candidate_section(lines, report.documents, limit, _format_document_line)
    else:
        lines.append("- (none)")

    # Servers (ranked by confidence, highest first)
    lines.append(_format_header("Servers", report.server_count))
    if report.servers:
        _append_candidate_section(lines, report.servers, limit, _format_server_line)
    else:
        lines.append("- (none)")

    # Warnings
    lines.append(_format_header("Warnings/Errors", report.warning_count))
    if report.warnings:
        for w in report.warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- (none)")

    # Summary
    lines.append(f"\n## Summary")
    lines.append(f"- Agent roots: {len(report.agent_roots)}")
    lines.append(f"- Projects discovered: {report.project_count}")
    lines.append(f"- Documents discovered: {report.document_count}")
    lines.append(f"- Servers discovered: {report.server_count}")
    lines.append(f"- Warnings: {report.warning_count}")

    return "\n".join(lines)

"""Source manifest domain model for local ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json

MANIFEST_VERSION = "1"

# source_type values that can be indexed now (text-based parsers exist)
INDEXABLE_SOURCE_TYPES = frozenset({"project_docs", "agent_session"})

# source_type values reserved for future parse_later support
PARSE_LATER_SOURCE_TYPES = frozenset(
    {"agent_memory", "operations_note", "binary_document"}
)

VALID_SOURCE_TYPES = INDEXABLE_SOURCE_TYPES | PARSE_LATER_SOURCE_TYPES
VALID_PRIVACY_SCOPES = frozenset({"local_private", "project_shared"})


@dataclass(frozen=True)
class SourceEntry:
    """A single source declared in a manifest."""

    source_type: str
    source_path: str
    source_tool: str = ""
    privacy_scope: str = "local_private"
    description: str = ""

    def __post_init__(self) -> None:
        if self.source_type not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"invalid source_type {self.source_type!r}; "
                f"expected one of {sorted(VALID_SOURCE_TYPES)}"
            )
        if self.privacy_scope not in VALID_PRIVACY_SCOPES:
            raise ValueError(
                f"invalid privacy_scope {self.privacy_scope!r}; "
                f"expected one of {sorted(VALID_PRIVACY_SCOPES)}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SourceEntry:
        source_type = _require_str(data, "source_type")
        source_path = _require_str(data, "source_path")
        return cls(
            source_type=source_type,
            source_path=source_path,
            source_tool=str(data.get("source_tool", "")),
            privacy_scope=str(data.get("privacy_scope", "local_private")),
            description=str(data.get("description", "")),
        )


@dataclass(frozen=True)
class SourceManifest:
    """A local ingestion manifest listing sources to index."""

    manifest_version: str
    project: str
    sources: list[SourceEntry]

    @classmethod
    def from_path(cls, path: Path) -> SourceManifest:
        """Load and validate a manifest JSON file."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FileNotFoundError(f"manifest not found: {path}")
        except OSError as exc:
            raise ValueError(f"cannot read manifest {path}: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in manifest {path}: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"manifest must be a JSON object, got {type(data).__name__}")

        version = str(data.get("manifest_version", ""))
        if version != MANIFEST_VERSION:
            raise ValueError(
                f"unsupported manifest_version {version!r}, expected {MANIFEST_VERSION!r}"
            )

        project = str(data.get("project", ""))
        if not project:
            raise ValueError("manifest requires a non-empty 'project' field")

        raw_sources = data.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ValueError("manifest requires a non-empty 'sources' list")

        sources: list[SourceEntry] = []
        for idx, item in enumerate(raw_sources):
            if not isinstance(item, dict):
                raise ValueError(f"source[{idx}] must be a JSON object, got {type(item).__name__}")
            sources.append(SourceEntry.from_dict(item))

        return cls(manifest_version=version, project=project, sources=sources)


@dataclass(frozen=True)
class IngestionRecord:
    """Normalized record produced by a parser, ready for indexing."""

    project: str
    source_type: str
    source_path: str
    source_tool: str
    text: str
    session_id: str | None = None
    created_at: str | None = None
    last_modified: str | None = None
    privacy_scope: str = "local_private"
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return Path(self.source_path).name


def _require_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest entry requires a non-empty string field {key!r}")
    return value.strip()

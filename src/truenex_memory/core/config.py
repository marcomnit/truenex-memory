"""Local project configuration for Truenex Memory."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DATA_DIR_NAME = ".truenex-memory"
DB_FILENAME = "truenex_memory.db"
DEFAULT_VECTOR_BACKEND = "sqlite"
DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_QDRANT_COLLECTION = "truenex_memory"


@dataclass(frozen=True)
class ProjectConfig:
    """Resolved local paths for one project memory store."""

    project_root: Path
    data_dir: Path
    db_path: Path
    exports_dir: Path
    backups_dir: Path
    vector_backend: str
    qdrant_url: str
    qdrant_collection: str


MemoryConfig = ProjectConfig
MemoryPaths = ProjectConfig


def resolve_project_config(project_root: Path | str = ".") -> ProjectConfig:
    """Return normalized local paths without creating files."""

    root = Path(project_root).resolve()
    data_dir = root / DATA_DIR_NAME
    vector_backend = os.getenv("TRUENEX_MEMORY_VECTOR_BACKEND", DEFAULT_VECTOR_BACKEND).strip().lower()
    if vector_backend not in {"sqlite", "qdrant"}:
        vector_backend = DEFAULT_VECTOR_BACKEND
    return ProjectConfig(
        project_root=root,
        data_dir=data_dir,
        db_path=data_dir / DB_FILENAME,
        exports_dir=data_dir / "exports",
        backups_dir=data_dir / "backups",
        vector_backend=vector_backend,
        qdrant_url=os.getenv("TRUENEX_MEMORY_QDRANT_URL", DEFAULT_QDRANT_URL).strip() or DEFAULT_QDRANT_URL,
        qdrant_collection=(
            os.getenv("TRUENEX_MEMORY_QDRANT_COLLECTION", DEFAULT_QDRANT_COLLECTION).strip()
            or DEFAULT_QDRANT_COLLECTION
        ),
    )


def ensure_project_dirs(config: ProjectConfig) -> None:
    """Create local data directories for a project."""

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.exports_dir.mkdir(parents=True, exist_ok=True)
    config.backups_dir.mkdir(parents=True, exist_ok=True)

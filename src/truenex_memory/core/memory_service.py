"""High-level local memory service used by CLI and adapters."""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.config import ensure_project_dirs, resolve_project_config
from truenex_memory.core.indexer import index_path
from truenex_memory.retrieval.semantic import HashingEmbedder
from truenex_memory.store.models import MemoryNode, RetrievalLog, SearchHit
from truenex_memory.store.qdrant_store import QdrantVectorStore, VectorStoreUnavailable
from truenex_memory.store.repository import MemoryRepository


class MemoryService:
    """Facade around local configuration, indexing and repository operations."""

    def __init__(self, project_root: Path | str = ".") -> None:
        self.config = resolve_project_config(project_root)
        self.embedder = HashingEmbedder()
        self.vector_store_status: dict[str, object] = {
            "backend": self.config.vector_backend,
            "active_backend": "sqlite",
            "qdrant_url": self.config.qdrant_url,
            "qdrant_collection": self.config.qdrant_collection,
            "available": False,
            "error": None,
        }
        vector_store = None
        if self.config.vector_backend == "qdrant":
            try:
                vector_store = QdrantVectorStore(
                    collection_name=self.config.qdrant_collection,
                    dimensions=self.embedder.dimensions,
                    url=self.config.qdrant_url,
                )
                vector_store.initialize()
                self.vector_store_status.update({"active_backend": "qdrant", "available": True})
            except VectorStoreUnavailable as exc:
                self.vector_store_status.update({"error": str(exc)})
        self.repository = MemoryRepository(
            self.config.db_path,
            embedder=self.embedder,
            vector_store=vector_store,
        )

    def init_project(self) -> None:
        ensure_project_dirs(self.config)
        self.repository.initialize()

    def add(self, content: str, *, memory_type: str = "note") -> str:
        self.init_project()
        return self.repository.add_memory(content, memory_type=memory_type)

    def index(self, path: Path | str) -> int:
        self.init_project()
        return index_path(Path(path), project_root=self.config.project_root, repository=self.repository)

    def search(self, query: str, *, top_k: int = 5, include_inactive: bool = False) -> list[SearchHit]:
        self.init_project()
        return self.repository.search(query, top_k=top_k, include_inactive=include_inactive)

    def list_memory_nodes(self, *, status: str | None = None) -> list[MemoryNode]:
        self.init_project()
        return self.repository.list_memory_nodes(status=status)

    def set_memory_status(self, memory_id: str, status: str) -> None:
        self.init_project()
        self.repository.set_memory_status(memory_id, status)

    def stats(self) -> dict[str, int]:
        self.init_project()
        return self.repository.stats()

    def list_retrieval_logs(self, *, limit: int = 20) -> list[RetrievalLog]:
        self.init_project()
        return self.repository.list_retrieval_logs(limit=limit)

    def get_retrieval_log(self, trace_id: str) -> RetrievalLog | None:
        self.init_project()
        return self.repository.get_retrieval_log(trace_id)

    @property
    def last_trace_id(self) -> str | None:
        return self.repository.last_trace_id

    def vector_status(self) -> dict[str, object]:
        """Return vector backend status without exposing project content."""

        return dict(self.vector_store_status)

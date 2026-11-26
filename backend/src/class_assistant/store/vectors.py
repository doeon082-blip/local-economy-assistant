"""Vector index behind a small interface so Chroma can be swapped (pgvector, FAISS, ...)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .. import config
from ..llm.base import Embedder


class ChunkIndex(Protocol):
    def add_chunks(self, doc_id: int, filename: str, chunks: list[Any]) -> None: ...

    def search(self, query: str, k: int = 6, doc_ids: list[int] | None = None) -> list[dict[str, Any]]: ...

    def delete_document(self, doc_id: int) -> None: ...

    def count(self) -> int: ...


class ChromaIndex:
    """Chroma-backed ChunkIndex. Vectors come from the injected Embedder."""

    def __init__(self, embedder: Embedder, persist_dir: Path | str | None = None, batch_size: int = 64) -> None:
        import chromadb
        from chromadb.config import Settings

        target = Path(persist_dir) if persist_dir is not None else config.VECTOR_DIR
        target.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(target), settings=Settings(anonymized_telemetry=False))
        self._collection = self._client.get_or_create_collection(name="class_chunks", metadata={"hnsw:space": "cosine"})
        self._embedder = embedder
        self._batch = batch_size

    def add_chunks(self, doc_id: int, filename: str, chunks: list[Any]) -> None:
        for offset in range(0, len(chunks), self._batch):
            batch = chunks[offset:offset + self._batch]
            self._collection.upsert(
                ids=[f"{doc_id}:{c.ordinal}" for c in batch],
                documents=[c.text for c in batch],
                metadatas=[
                    {"doc_id": doc_id, "ordinal": c.ordinal, "locator": c.locator, "filename": filename} for c in batch
                ],
                embeddings=self._embedder.embed([c.text for c in batch]),
            )

    def search(self, query: str, k: int = 6, doc_ids: list[int] | None = None) -> list[dict[str, Any]]:
        total = self._collection.count()
        if total == 0:
            return []
        where = {"doc_id": {"$in": list(doc_ids)}} if doc_ids else None
        result = self._collection.query(
            query_embeddings=self._embedder.embed([query]),
            n_results=min(k, total),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for text, meta, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
            hits.append({**meta, "text": text, "distance": float(distance)})
        return hits

    def delete_document(self, doc_id: int) -> None:
        self._collection.delete(where={"doc_id": doc_id})

    def count(self) -> int:
        return self._collection.count()

    def close(self) -> None:
        system = getattr(self._client, "_system", None)
        if system is not None and hasattr(system, "stop"):
            system.stop()

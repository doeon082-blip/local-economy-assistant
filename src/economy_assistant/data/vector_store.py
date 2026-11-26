"""Persistent Chroma vector store with keep-alive pooled parallel embeddings."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chromadb
from chromadb.config import Settings
import httpx

from ..config import VECTOR_DIR, embed_batch_size, embed_workers, ollama_keep_alive, ollama_url

EMBED_MODEL = "nomic-embed-text"


class VectorStore:
    def __init__(self, persist_dir: Path | str | None = None) -> None:
        target_dir = Path(persist_dir) if persist_dir is not None else VECTOR_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(target_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name="bok_economic_statistics",
            metadata={"hnsw:space": "cosine"},
        )
        self._http = httpx.Client(
            timeout=httpx.Timeout(90.0, connect=5.0),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        endpoint = ollama_url() + "/api/embed"
        try:
            response = self._http.post(
                endpoint,
                json={"model": EMBED_MODEL, "input": texts, "keep_alive": ollama_keep_alive()},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(
                "로컬 임베딩 모델을 사용할 수 없습니다. `ollama pull nomic-embed-text`를 실행하세요."
            ) from exc
        vectors = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise RuntimeError("Ollama 임베딩 응답 형식이 올바르지 않습니다.")
        return vectors

    @staticmethod
    def _format_doc(row: dict[str, str]) -> str:
        return (
            f"한국은행 ECOS 경제통계. 지표: {row['stat_name']}. 항목: {row['item_name']}. "
            f"주기: {row['cycle']}. 기간: {row['time']}. 값: {row['value']} {row['unit']}."
        )

    def _filter_changed(self, items: list[tuple[str, dict[str, str], str]]) -> list[tuple[str, dict[str, str], str]]:
        if not items or self.collection.count() == 0:
            return items
        existing_docs: dict[str, str] = {}
        ids = [doc_id for doc_id, _, _ in items]
        for offset in range(0, len(ids), 500):
            chunk_ids = ids[offset:offset + 500]
            res = self.collection.get(ids=chunk_ids, include=["documents"])
            for existing_id, existing_doc in zip(res.get("ids") or [], res.get("documents") or []):
                if isinstance(existing_doc, str):
                    existing_docs[existing_id] = existing_doc
        return [(doc_id, row, doc) for doc_id, row, doc in items if existing_docs.get(doc_id) != doc]

    def upsert(self, rows: list[dict[str, str]], source_url: str) -> int:
        deduped: dict[str, dict[str, str]] = {}
        for row in rows:
            doc_id = f"{row['stat_code']}:{row['item_code']}:{row['cycle']}:{row['time']}"
            deduped[doc_id] = row
        if not deduped:
            return 0

        items = [(doc_id, row, self._format_doc(row)) for doc_id, row in deduped.items()]
        changed = self._filter_changed(items)
        if not changed:
            return len(items)

        batch_size = embed_batch_size()
        batches = [changed[offset:offset + batch_size] for offset in range(0, len(changed), batch_size)]

        def _embed_batch(batch: list[tuple[str, dict[str, str], str]]) -> tuple[list[str], list[str], list[dict[str, str]], list[list[float]]]:
            ids = [doc_id for doc_id, _, _ in batch]
            docs = [doc for _, _, doc in batch]
            metadatas = [
                {
                    "stat_code": r["stat_code"],
                    "stat_name": r["stat_name"],
                    "item_code": r["item_code"],
                    "item_name": r["item_name"],
                    "cycle": r["cycle"],
                    "period": r["time"],
                    "value": r["value"],
                    "unit": r["unit"],
                    "source": source_url,
                }
                for _, r, _ in batch
            ]
            embeddings = self._embed(docs)
            return ids, docs, metadatas, embeddings

        workers = min(embed_workers(), len(batches))
        if workers <= 1:
            results = [_embed_batch(batch) for batch in batches]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(_embed_batch, batches))

        for ids, docs, metadatas, embeddings in results:
            self.collection.upsert(ids=ids, documents=docs, metadatas=metadatas, embeddings=embeddings)
        return len(items)

    def search(self, query: str, limit: int = 8) -> list[dict[str, str]]:
        total = self.collection.count()
        if total == 0:
            return []
        result = self.collection.query(
            query_embeddings=self._embed([query]),
            n_results=min(limit, total),
            include=["documents", "metadatas", "distances"],
        )
        documents = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            {**meta, "document": doc, "distance": str(distance)}
            for doc, meta, distance in zip(documents, metas, distances)
        ]

    def stats(self) -> dict[str, int]:
        return {"observations": self.collection.count()}

    def close(self) -> None:
        self._http.close()
        system = getattr(self.client, "_system", None)
        if system is not None and hasattr(system, "stop"):
            system.stop()

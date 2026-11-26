"""Dependency container and the document library (upload -> extract -> chunk -> store -> index)."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePath
import re
from typing import Any

from . import config
from .ingest import ExtractionError, chunk_segments, extract, supported_extensions
from .llm import build_provider
from .llm.base import LLMProvider
from .store import ChromaIndex, ChunkIndex, Database


class UploadError(ValueError):
    """Invalid upload. ``reason`` is one of: empty, unsupported, too_large, unreadable."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def sanitize_filename(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip().strip(".")
    return base[:120] or "untitled"


class Library:
    def __init__(self, db: Database, index: ChunkIndex, max_bytes: int | None = None) -> None:
        self._db = db
        self._index = index
        self._max_bytes = max_bytes

    def add_file(self, filename: str, data: bytes) -> tuple[dict[str, Any], bool]:
        """Returns ``(document, created)``. Re-uploading identical bytes returns the existing document."""
        name = sanitize_filename(filename)
        ext = PurePath(name).suffix.lower()
        if ext not in supported_extensions():
            raise UploadError("unsupported", f"지원하지 않는 형식입니다. 가능한 형식: {', '.join(supported_extensions())}")
        if not data:
            raise UploadError("empty", "빈 파일입니다.")
        limit = self._max_bytes if self._max_bytes is not None else config.max_upload_bytes()
        if len(data) > limit:
            raise UploadError("too_large", f"파일이 너무 큽니다. (최대 {limit // (1024 * 1024)}MB)")

        digest = hashlib.sha256(data).hexdigest()
        existing = self._db.find_document_by_hash(digest)
        if existing:
            return existing, False
        try:
            chunks = chunk_segments(extract(name, data))
        except ExtractionError as exc:
            raise UploadError("unreadable", str(exc)) from exc

        document = self._db.add_document(name, ext.lstrip("."), digest, chunks)
        try:
            self._index.add_chunks(document["id"], name, chunks)
        except Exception:
            # Keep SQLite and the vector index consistent: no half-ingested documents.
            self._db.delete_document(document["id"])
            self._index.delete_document(document["id"])
            raise
        return document, True

    def remove(self, doc_id: int) -> bool:
        removed = self._db.delete_document(doc_id)
        if removed:
            self._index.delete_document(doc_id)
        return removed


@dataclass
class Services:
    db: Database
    index: ChunkIndex
    llm: LLMProvider
    library: Library


def build_default_services() -> Services:
    llm, embedder = build_provider()
    db = Database()
    index = ChromaIndex(embedder)
    return Services(db=db, index=index, llm=llm, library=Library(db, index))

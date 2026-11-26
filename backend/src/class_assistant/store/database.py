"""SQLite persistence: documents, chunks, quiz items, attempts, generated notes."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .. import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    chunk_count INTEGER NOT NULL,
    char_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    locator TEXT NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (doc_id, ordinal)
);
CREATE TABLE IF NOT EXISTS quiz_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    chunk_id INTEGER,
    kind TEXT NOT NULL,              -- 'mcq' | 'short'
    topic TEXT NOT NULL,
    question TEXT NOT NULL,
    choices TEXT NOT NULL,           -- JSON list ([] for short answer)
    answer TEXT NOT NULL,            -- mcq: choice index as text, short: expected answer
    explanation TEXT NOT NULL,
    source_text TEXT NOT NULL,       -- grounding passage shown to the learner for verification
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    user_answer TEXT NOT NULL,
    correct INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    doc_id INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- e.g. 'summary'
    content TEXT NOT NULL,           -- JSON
    created_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (doc_id);
CREATE INDEX IF NOT EXISTS idx_quiz_doc ON quiz_items (doc_id);
CREATE INDEX IF NOT EXISTS idx_attempts_quiz ON attempts (quiz_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _placeholders(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


class Database:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else config.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _all(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # --- documents -------------------------------------------------------
    def find_document_by_hash(self, sha256: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM documents WHERE sha256 = ?", (sha256,))

    def add_document(self, filename: str, kind: str, sha256: str, chunks: list[Any]) -> dict[str, Any]:
        """``chunks`` are objects with ``ordinal``, ``locator`` and ``text`` (ingest.Chunk)."""
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO documents (filename, kind, sha256, chunk_count, char_count, created_at) VALUES (?,?,?,?,?,?)",
                (filename, kind, sha256, len(chunks), sum(len(c.text) for c in chunks), _now()),
            )
            doc_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO chunks (doc_id, ordinal, locator, text) VALUES (?,?,?,?)",
                [(doc_id, c.ordinal, c.locator, c.text) for c in chunks],
            )
        return self.get_document(doc_id) or {}

    def get_document(self, doc_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM documents WHERE id = ?", (doc_id,))

    def list_documents(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM documents ORDER BY id DESC")

    def delete_document(self, doc_id: int) -> bool:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM attempts WHERE quiz_id IN (SELECT id FROM quiz_items WHERE doc_id = ?)", (doc_id,))
            for table in ("quiz_items", "notes", "chunks"):
                conn.execute(f"DELETE FROM {table} WHERE doc_id = ?", (doc_id,))
            deleted = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,)).rowcount
        return deleted > 0

    # --- chunks ----------------------------------------------------------
    def get_chunks(self, doc_id: int) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM chunks WHERE doc_id = ? ORDER BY ordinal", (doc_id,))

    def list_chunks(self, doc_ids: list[int] | None, min_chars: int = 1) -> list[dict[str, Any]]:
        where, params = ["length(c.text) >= ?"], [min_chars]
        if doc_ids:
            where.append(f"c.doc_id IN ({_placeholders(doc_ids)})")
            params += doc_ids
        return self._all(
            "SELECT c.*, d.filename FROM chunks c JOIN documents d ON d.id = c.doc_id "
            f"WHERE {' AND '.join(where)} ORDER BY c.doc_id, c.ordinal",
            params,
        )

    # --- quiz ------------------------------------------------------------
    def add_quiz_items(self, items: list[dict[str, Any]]) -> list[int]:
        ids: list[int] = []
        with closing(self._connect()) as conn, conn:
            for item in items:
                cursor = conn.execute(
                    "INSERT INTO quiz_items (doc_id, chunk_id, kind, topic, question, choices, answer, explanation, "
                    "source_text, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["doc_id"], item.get("chunk_id"), item["kind"], item["topic"], item["question"],
                        json.dumps(item.get("choices", []), ensure_ascii=False), str(item["answer"]),
                        item.get("explanation", ""), item["source_text"], _now(),
                    ),
                )
                ids.append(int(cursor.lastrowid))
        return ids

    def question_texts(self, doc_ids: list[int] | None = None, limit: int = 500) -> list[str]:
        """Most recent stored questions (used to avoid asking the same thing twice)."""
        where, params = "", []
        if doc_ids:
            where, params = f"WHERE doc_id IN ({_placeholders(doc_ids)})", list(doc_ids)
        rows = self._all(f"SELECT question FROM quiz_items {where} ORDER BY id DESC LIMIT ?", [*params, limit])
        return [r["question"] for r in rows]

    def chunk_questions(self, chunk_ids: list[int]) -> list[tuple[int, str]]:
        if not chunk_ids:
            return []
        rows = self._all(
            f"SELECT chunk_id, question FROM quiz_items WHERE chunk_id IN ({_placeholders(chunk_ids)}) ORDER BY id",
            chunk_ids,
        )
        return [(r["chunk_id"], r["question"]) for r in rows]

    def get_quiz_item(self, quiz_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM quiz_items WHERE id = ?", (quiz_id,))
        if row:
            row["choices"] = json.loads(row["choices"])
        return row

    def record_attempt(self, quiz_id: int, user_answer: str, correct: bool) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO attempts (quiz_id, user_answer, correct, created_at) VALUES (?,?,?,?)",
                (quiz_id, user_answer, int(correct), _now()),
            )

    def review_candidates(self, limit: int, doc_ids: list[int] | None = None) -> list[dict[str, Any]]:
        """Wrong-last first (most misses first), then never-attempted, then least-practised."""
        where, params = "", []
        if doc_ids:
            where, params = f"WHERE q.doc_id IN ({_placeholders(doc_ids)})", list(doc_ids)
        rows = self._all(
            "SELECT q.*, COALESCE(a.attempts, 0) AS attempts, COALESCE(a.wrong, 0) AS wrong, "
            "(SELECT correct FROM attempts WHERE quiz_id = q.id ORDER BY id DESC LIMIT 1) AS last_correct "
            "FROM quiz_items q LEFT JOIN ("
            "  SELECT quiz_id, COUNT(*) AS attempts, SUM(1 - correct) AS wrong FROM attempts GROUP BY quiz_id"
            ") a ON a.quiz_id = q.id " + where,
            params,
        )

        def priority(row: dict[str, Any]) -> tuple[int, int, int]:
            if row["last_correct"] == 0:
                return (0, -int(row["wrong"]), row["id"])
            if row["attempts"] == 0:
                return (1, 0, row["id"])
            return (2, int(row["attempts"]), row["id"])

        rows.sort(key=priority)
        for row in rows[:limit]:
            row["choices"] = json.loads(row["choices"])
        return rows[:limit]

    def progress_by_topic(self) -> list[dict[str, Any]]:
        return self._all(
            "SELECT q.topic AS topic, COUNT(DISTINCT q.id) AS items, COUNT(a.id) AS attempts, "
            "COALESCE(SUM(a.correct), 0) AS correct "
            "FROM quiz_items q LEFT JOIN attempts a ON a.quiz_id = q.id GROUP BY q.topic ORDER BY q.topic"
        )

    # --- notes (cached generated content) --------------------------------
    def save_note(self, doc_id: int, kind: str, content: dict[str, Any]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO notes (doc_id, kind, content, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(doc_id, kind) DO UPDATE SET content = excluded.content, created_at = excluded.created_at",
                (doc_id, kind, json.dumps(content, ensure_ascii=False), _now()),
            )

    def get_note(self, doc_id: int, kind: str) -> dict[str, Any] | None:
        row = self._one("SELECT content FROM notes WHERE doc_id = ? AND kind = ?", (doc_id, kind))
        return json.loads(row["content"]) if row else None

    def stats(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            return {
                name: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for name, table in (("documents", "documents"), ("chunks", "chunks"), ("quiz_items", "quiz_items"), ("attempts", "attempts"))
            }

from __future__ import annotations

import gc
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from economy_assistant import api
from economy_assistant.data.sqlite_store import StatisticsDatabase
from economy_assistant.data.vector_store import VectorStore


def _run_coro(coro):
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    raise AssertionError("Coroutine unexpectedly yielded")


def _make_request(path: str = "/api/status", client_ip: str = "127.0.0.1", headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": raw_headers,
        "client": (client_ip, 12345),
        "scheme": "http",
        "server": ("127.0.0.1", 8765),
        "query_string": b"",
    }
    return Request(scope)


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp = Path(self._tmpdir.name)
        self._db = StatisticsDatabase(db_path=tmp / "ecos.sqlite3")
        self._store = VectorStore(persist_dir=tmp / "chroma")
        self._orig_db = api._db
        self._orig_store = api._store
        api._db = self._db
        api._store = self._store

    def tearDown(self) -> None:
        self._store.close()
        api._db = self._orig_db
        api._store = self._orig_store
        del self._store
        gc.collect()
        self._tmpdir.cleanup()

    def test_healthz_and_status(self) -> None:
        self.assertEqual(api.healthz(), {"status": "ok"})

        with patch("economy_assistant.api.ollama_status", return_value={"connected": False, "model": "qwen3.5:4b"}):
            res = api.status()
        body = json.loads(res.body.decode("utf-8"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["vector_count"], 0)
        self.assertEqual(body["db_count"], 0)
        self.assertEqual(body["max_sync_limit"], 50000)
        self.assertFalse(body["sync_requires_key"])
        self.assertTrue(body["approval_token"])

    def test_status_hides_approval_token_when_sync_admin_key_set(self) -> None:
        with patch.dict(os.environ, {"SYNC_ADMIN_KEY": "my-admin-secret"}, clear=False):
            with patch("economy_assistant.api.ollama_status", return_value={"connected": False, "model": "qwen3.5:4b"}):
                res = api.status()
        body = json.loads(res.body.decode("utf-8"))
        self.assertTrue(body["sync_requires_key"])
        self.assertIsNone(body["approval_token"])

    def test_chat_and_stream_reject_blank_question_and_stream_tokens(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            api.chat(api.ChatRequest(question="   ", history=[]))
        self.assertEqual(ctx.exception.status_code, 422)

        captured_messages = []

        def fake_chat(messages):
            captured_messages.extend(messages)
            return "테스트 응답"

        with patch("economy_assistant.services.economic_assistant.chat", side_effect=fake_chat):
            res = api.chat(
                api.ChatRequest(
                    question="기준금리가 뭐야?",
                    history=[
                        {"role": "user", "content": "안녕"},
                        {"role": "assistant", "content": "안녕하세요"},
                    ],
                )
            )
        self.assertEqual(res["answer"], "테스트 응답")
        self.assertEqual([m["role"] for m in captured_messages], ["system", "user", "assistant", "user"])

        with patch("economy_assistant.services.economic_assistant.chat_stream", return_value=iter(["안녕", "하세요"])):
            stream_res = api.chat_stream_endpoint(api.ChatRequest(question="설명해줘", history=[]))
            self.assertEqual(stream_res.media_type, "application/x-ndjson")

        events = [json.loads(chunk) for chunk in api._stream_ndjson([], iter(["안녕", "하세요"]))]
        self.assertEqual(events[0]["type"], "meta")
        self.assertEqual([e["content"] for e in events if e["type"] == "delta"], ["안녕", "하세요"])
        self.assertEqual(events[-1]["type"], "done")

    def test_sync_and_batch_save_immediately_to_sqlite_and_vector_store(self) -> None:
        with self.assertRaises(HTTPException) as unauth:
            api.sync_data(api.SyncRequest(), _make_request("/api/data/sync"))
        self.assertEqual(unauth.exception.status_code, 403)

        fake_rows = [
            {
                "stat_code": "722Y001",
                "stat_name": "한국은행 기준금리",
                "item_code": "0101000",
                "item_name": "기준금리",
                "cycle": "D",
                "time": "20260102",
                "value": "2.75",
                "unit": "%",
            }
        ]
        with patch("economy_assistant.api.fetch_series", return_value=(fake_rows, "https://ecos.bok.or.kr/api/")):
            with patch.object(self._store, "_embed", side_effect=lambda texts: [[0.1, 0.2] for _ in texts]):
                with patch.dict(os.environ, {"SYNC_ADMIN_KEY": "admin-123"}, clear=False):
                    ok = api.sync_data(
                        api.SyncRequest(),
                        _make_request("/api/data/sync", headers={"X-Local-Approval": "admin-123"}),
                    )
                    self.assertEqual(ok["db_saved"], 1)
                    self.assertEqual(ok["db_count"], 1)
                    self.assertEqual(ok["vector_count"], 1)

        batch_rows = [
            (
                {"stat_code": "731Y001", "item_code": "", "cycle": "M", "start": "202601", "end": "202610"},
                [
                    {
                        "stat_code": "731Y001",
                        "stat_name": "주요국 환율",
                        "item_code": "0000001",
                        "item_name": "원/미국달러",
                        "cycle": "M",
                        "time": "202610",
                        "value": "1380.5",
                        "unit": "원",
                    }
                ],
            )
        ]
        with patch("economy_assistant.api.fetch_many", return_value=batch_rows):
            with patch.object(self._store, "_embed", side_effect=lambda texts: [[0.2, 0.3] for _ in texts]):
                with patch.dict(os.environ, {"SYNC_ADMIN_KEY": "admin-123"}, clear=False):
                    batch_res = api.sync_batch(
                        api.BatchSyncRequest(items=[api.SyncRequest(stat_code="731Y001", item_code="", cycle="M", start="202601", end="202610")]),
                        _make_request("/api/data/sync/batch", headers={"X-Local-Approval": "admin-123"}),
                    )
                    self.assertEqual(batch_res["series_count"], 1)
                    self.assertEqual(batch_res["db_count"], 2)
                    self.assertEqual(batch_res["vector_count"], 2)

    def test_loopback_middleware_respects_allow_remote_access(self) -> None:
        async def ok_next(_request: Request) -> JSONResponse:
            return JSONResponse({"ok": True})

        with patch.dict(os.environ, {"ALLOW_REMOTE_ACCESS": "false"}, clear=False):
            blocked = _run_coro(api.loopback_only(_make_request("/api/status", "198.51.100.10"), ok_next))
            self.assertEqual(blocked.status_code, 403)
            health = _run_coro(api.loopback_only(_make_request("/healthz", "198.51.100.10"), ok_next))
            self.assertEqual(health.status_code, 200)

        with patch.dict(os.environ, {"ALLOW_REMOTE_ACCESS": "true"}, clear=False):
            allowed = _run_coro(api.loopback_only(_make_request("/api/status", "198.51.100.10"), ok_next))
            self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()

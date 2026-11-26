from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from economy_assistant.services import ollama_client
from economy_assistant.services.ollama_client import OllamaError, chat, chat_stream, status


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ndjson(*chunks: object) -> bytes:
    return "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks).encode()


class OllamaClientTests(unittest.TestCase):
    def test_chat_returns_content_and_disables_thinking(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"message": {"content": "안녕"}})

        with patch.object(ollama_client, "_HTTP", _client(handler)):
            self.assertEqual(chat([{"role": "user", "content": "hi"}]), "안녕")
        self.assertIs(seen["stream"], False)
        self.assertIs(seen["think"], False)

    def test_chat_maps_http_and_format_errors(self) -> None:
        with patch.object(ollama_client, "_HTTP", _client(lambda r: httpx.Response(404))):
            with self.assertRaisesRegex(OllamaError, "HTTP 404"):
                chat([])
        with patch.object(ollama_client, "_HTTP", _client(lambda r: httpx.Response(200, json={"x": 1}))):
            with self.assertRaisesRegex(OllamaError, "형식"):
                chat([])

        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with patch.object(ollama_client, "_HTTP", _client(boom)):
            with self.assertRaisesRegex(OllamaError, "연결"):
                chat([])

    def test_chat_stream_yields_tokens_until_done(self) -> None:
        body = _ndjson(
            {"message": {"content": "가"}},
            {"message": {"content": ""}},
            {"message": {"content": "나"}, "done": True},
            {"message": {"content": "무시"}},
        )
        with patch.object(ollama_client, "_HTTP", _client(lambda r: httpx.Response(200, content=body))):
            self.assertEqual(list(chat_stream([])), ["가", "나"])

    def test_chat_stream_surfaces_server_error_chunk_and_bad_json(self) -> None:
        err = _ndjson({"error": "model not found"})
        with patch.object(ollama_client, "_HTTP", _client(lambda r: httpx.Response(200, content=err))):
            with self.assertRaisesRegex(OllamaError, "model not found"):
                list(chat_stream([]))
        with patch.object(ollama_client, "_HTTP", _client(lambda r: httpx.Response(200, content=b"not json\n"))):
            with self.assertRaises(OllamaError):
                list(chat_stream([]))

    def test_status_reports_models_and_disconnect(self) -> None:
        tags = {"models": [{"name": ollama_client.model_name()}, {"name": "nomic-embed-text:latest"}]}
        with patch.object(ollama_client, "_HTTP", _client(lambda r: httpx.Response(200, json=tags))):
            result = status()
        self.assertTrue(result["connected"])
        self.assertTrue(result["model_available"])
        self.assertTrue(result["embed_model_available"])

        with patch.object(ollama_client, "_HTTP", _client(lambda r: httpx.Response(500))):
            result = status()
        self.assertFalse(result["connected"])
        self.assertEqual(result["models"], [])


if __name__ == "__main__":
    unittest.main()

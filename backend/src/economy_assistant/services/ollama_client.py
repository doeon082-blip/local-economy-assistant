"""HTTP client for configured Ollama server with keep-alive pooling and streaming."""
from __future__ import annotations

from collections.abc import Iterator
import json

import httpx

from ..config import (
    model_name,
    ollama_keep_alive,
    ollama_num_ctx,
    ollama_num_predict,
    ollama_url,
)
from ..data.vector_store import EMBED_MODEL


class OllamaError(RuntimeError):
    pass


_HTTP = httpx.Client(
    timeout=httpx.Timeout(180.0, connect=5.0),
    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
)


def _is_model_present(target: str, names: list[str]) -> bool:
    if target in names:
        return True
    if ":" not in target and f"{target}:latest" in names:
        return True
    return False


def _chat_payload(messages: list[dict[str, str]], *, stream: bool) -> dict[str, object]:
    return {
        "model": model_name(),
        "messages": messages,
        "stream": stream,
        "think": False,
        "keep_alive": ollama_keep_alive(),
        "options": {
            "num_ctx": ollama_num_ctx(),
            "num_predict": ollama_num_predict(),
            "temperature": 0.3,
        },
    }


def chat(messages: list[dict[str, str]]) -> str:
    try:
        response = _HTTP.post(
            ollama_url() + "/api/chat",
            json=_chat_payload(messages, stream=False),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise OllamaError(f"Ollama 요청 실패 (HTTP {exc.response.status_code}). 모델 설치 여부를 확인하세요.") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise OllamaError("Ollama에 연결할 수 없습니다. 실행 상태를 확인하세요.") from exc
    answer = payload.get("message", {}).get("content") if isinstance(payload, dict) else None
    if not isinstance(answer, str):
        raise OllamaError("Ollama 응답 형식이 예상과 다릅니다.")
    return answer


def chat_stream(messages: list[dict[str, str]]) -> Iterator[str]:
    try:
        with _HTTP.stream(
            "POST",
            ollama_url() + "/api/chat",
            json=_chat_payload(messages, stream=True),
            headers={"Accept": "application/x-ndjson"},
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    raise OllamaError(str(chunk["error"]))
                token = (chunk.get("message") or {}).get("content")
                if isinstance(token, str) and token:
                    yield token
                if chunk.get("done"):
                    break
    except httpx.HTTPStatusError as exc:
        raise OllamaError(f"Ollama 요청 실패 (HTTP {exc.response.status_code}). 모델 설치 여부를 확인하세요.") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise OllamaError("Ollama에 연결할 수 없습니다. 실행 상태를 확인하세요.") from exc


def status() -> dict[str, object]:
    selected = model_name()
    try:
        response = _HTTP.get(
            ollama_url() + "/api/tags",
            headers={"Accept": "application/json"},
            timeout=3.0,
        )
        response.raise_for_status()
        payload = response.json()
        names = [item.get("name", "") for item in payload.get("models", []) if isinstance(item, dict)]
        return {
            "connected": True,
            "model": selected,
            "model_available": _is_model_present(selected, names),
            "embed_model": EMBED_MODEL,
            "embed_model_available": _is_model_present(EMBED_MODEL, names),
            "models": names,
        }
    except Exception:
        return {
            "connected": False,
            "model": selected,
            "model_available": False,
            "embed_model": EMBED_MODEL,
            "embed_model_available": False,
            "models": [],
        }

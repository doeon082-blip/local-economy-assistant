"""Ollama implementation of LLMProvider + Embedder (adapted from economy_assistant)."""
from __future__ import annotations

from collections.abc import Iterator
import json

import httpx

from .. import config
from .base import LLMError, Message


class OllamaProvider:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._embed_model = embed_model
        self._http = client or httpx.Client(timeout=httpx.Timeout(180.0, connect=5.0))

    # Resolved lazily so .env changes and config validation errors surface at call time.
    @property
    def base_url(self) -> str:
        return self._base_url or config.ollama_url()

    @property
    def model(self) -> str:
        return self._model or config.chat_model()

    @property
    def embed_model(self) -> str:
        return self._embed_model or config.embed_model()

    def _payload(self, messages: list[Message], *, stream: bool, json_mode: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "think": False,
            "keep_alive": config.ollama_keep_alive(),
            "options": {
                "num_ctx": config.ollama_num_ctx(),
                "num_predict": config.ollama_num_predict(),
                "temperature": 0.3,
            },
        }
        if json_mode:
            payload["format"] = "json"
        return payload

    def chat(self, messages: list[Message], *, json_mode: bool = False) -> str:
        try:
            response = self._http.post(
                self.base_url + "/api/chat", json=self._payload(messages, stream=False, json_mode=json_mode)
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content")
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Ollama 요청 실패 (HTTP {exc.response.status_code}). 모델 설치 여부를 확인하세요.") from exc
        except (httpx.RequestError, ValueError, AttributeError) as exc:
            raise LLMError("Ollama에 연결할 수 없습니다. 실행 상태를 확인하세요.") from exc
        if not isinstance(content, str):
            raise LLMError("Ollama 응답 형식이 예상과 다릅니다.")
        return content

    def stream(self, messages: list[Message]) -> Iterator[str]:
        try:
            with self._http.stream(
                "POST", self.base_url + "/api/chat", json=self._payload(messages, stream=True)
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("error"):
                        raise LLMError(str(chunk["error"]))
                    token = (chunk.get("message") or {}).get("content")
                    if isinstance(token, str) and token:
                        yield token
                    if chunk.get("done"):
                        break
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Ollama 요청 실패 (HTTP {exc.response.status_code}).") from exc
        except (httpx.RequestError, ValueError) as exc:
            raise LLMError("Ollama에 연결할 수 없습니다. 실행 상태를 확인하세요.") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._http.post(self.base_url + "/api/embed", json={"model": self.embed_model, "input": texts})
            response.raise_for_status()
            vectors = response.json().get("embeddings")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise LLMError(f"임베딩 모델을 사용할 수 없습니다. `ollama pull {self.embed_model}`를 실행하세요.") from exc
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise LLMError("임베딩 응답 형식이 올바르지 않습니다.")
        return vectors

    def warmup(self) -> None:
        """Load both models into memory ahead of the first user request. Best effort."""
        try:
            self._http.post(
                self.base_url + "/api/chat",
                json={**self._payload([{"role": "user", "content": "ok"}], stream=False), "options": {"num_predict": 1}},
            ).raise_for_status()
            self.embed(["warmup"])
        except Exception:
            pass

    def health(self) -> dict[str, object]:
        try:
            response = self._http.get(self.base_url + "/api/tags", timeout=3.0)
            response.raise_for_status()
            names = [m.get("name", "") for m in response.json().get("models", []) if isinstance(m, dict)]
        except Exception:
            return {
                "provider": "ollama",
                "connected": False,
                "model": self.model,
                "model_available": False,
                "embed_model": self.embed_model,
                "embed_model_available": False,
            }

        def present(target: str) -> bool:
            return target in names or (":" not in target and f"{target}:latest" in names)

        return {
            "provider": "ollama",
            "connected": True,
            "model": self.model,
            "model_available": present(self.model),
            "embed_model": self.embed_model,
            "embed_model_available": present(self.embed_model),
        }

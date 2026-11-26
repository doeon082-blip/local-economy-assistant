"""Provider-neutral interfaces. Features depend on these, never on a concrete backend."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}


class LLMError(RuntimeError):
    """Raised for any model/provider failure; the API maps it to HTTP 503."""


class LLMProvider(Protocol):
    def chat(self, messages: list[Message], *, json_mode: bool = False, model: str | None = None) -> str: ...

    def stream(self, messages: list[Message]) -> Iterator[str]: ...

    def health(self) -> dict[str, object]: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

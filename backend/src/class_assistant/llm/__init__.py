"""LLM provider registry.

Add a backend (cloud API, llama.cpp, ...) by registering a factory::

    from class_assistant.llm import register_provider
    register_provider("myprovider", lambda: (MyProvider(), MyEmbedder()))

then set ``LLM_PROVIDER=myprovider``.
"""
from __future__ import annotations

from collections.abc import Callable

from .. import config
from .base import Embedder, LLMError, LLMProvider, Message
from .ollama import OllamaProvider

ProviderFactory = Callable[[], tuple[LLMProvider, Embedder]]
_PROVIDERS: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _PROVIDERS[name.lower()] = factory


def _ollama() -> tuple[LLMProvider, Embedder]:
    provider = OllamaProvider()
    return provider, provider


register_provider("ollama", _ollama)


def build_provider() -> tuple[LLMProvider, Embedder]:
    name = config.llm_provider()
    if name not in _PROVIDERS:
        raise LLMError(f"알 수 없는 LLM_PROVIDER: {name} (등록됨: {', '.join(sorted(_PROVIDERS))})")
    return _PROVIDERS[name]()


__all__ = ["Embedder", "LLMError", "LLMProvider", "Message", "OllamaProvider", "build_provider", "register_provider"]

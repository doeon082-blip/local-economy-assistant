"""Feature plug-in contract.

A feature is a class registered with ``@register_feature``. The API exposes whatever is
registered at ``/api/features`` and routes chat modes automatically, so a new capability
(e.g. flashcard export, assignment helper) only needs a new module here.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, ClassVar

from ..llm.base import Message

if TYPE_CHECKING:
    from ..services import Services

FEATURES: dict[str, type["Feature"]] = {}


class Feature:
    name: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]

    def __init__(self, services: "Services") -> None:
        self.services = services

    @classmethod
    def info(cls) -> dict[str, Any]:
        return {"name": cls.name, "title": cls.title, "description": cls.description, "chat": issubclass(cls, ChatFeature)}


class ChatFeature(Feature):
    """A feature that answers in a streamed conversation (selected with ``mode`` in /api/chat/stream)."""

    def start(self, question: str, history: list[Message], **options: Any) -> tuple[dict[str, Any], Iterator[str]]:
        """Returns ``(meta, token_iterator)``; ``meta`` is sent to the client before the first token."""
        raise NotImplementedError


def register_feature(cls: type[Feature]) -> type[Feature]:
    FEATURES[cls.name] = cls
    return cls


def build_features(services: "Services") -> dict[str, Feature]:
    return {name: cls(services) for name, cls in FEATURES.items()}


def bounded_history(history: list[Message], turns: int = 6, max_chars: int = 2000) -> list[Message]:
    """Last ``turns`` user/assistant messages only; any other role is dropped (prompt-injection guard)."""
    return [
        {"role": m["role"], "content": m["content"][:max_chars]}
        for m in history[-turns:]
        if m.get("role") in {"user", "assistant"} and isinstance(m.get("content"), str)
    ]

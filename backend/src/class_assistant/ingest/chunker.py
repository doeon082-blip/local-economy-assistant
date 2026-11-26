"""Split extracted segments into retrieval-sized chunks that keep their citation locator."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .extractors import Segment

DEFAULT_MAX_CHARS = 900
DEFAULT_OVERLAP = 120


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    locator: str


def _hard_split(text: str, max_chars: int, overlap: int) -> list[str]:
    step = max(1, max_chars - overlap)
    pieces = [text[i:i + max_chars].strip() for i in range(0, len(text), step)]
    return [p for p in pieces if p]


def chunk_text(text: str, max_chars: int = DEFAULT_MAX_CHARS, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    if overlap >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_hard_split(paragraph, max_chars, overlap))
        elif not buffer:
            buffer = paragraph
        elif len(buffer) + 2 + len(paragraph) <= max_chars:
            buffer = f"{buffer}\n\n{paragraph}"
        else:
            chunks.append(buffer)
            buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return chunks


def chunk_segments(segments: list[Segment], max_chars: int = DEFAULT_MAX_CHARS, overlap: int = DEFAULT_OVERLAP) -> list[Chunk]:
    chunks: list[Chunk] = []
    for segment in segments:
        for piece in chunk_text(segment.text, max_chars, overlap):
            chunks.append(Chunk(ordinal=len(chunks), text=piece, locator=segment.locator))
    return chunks

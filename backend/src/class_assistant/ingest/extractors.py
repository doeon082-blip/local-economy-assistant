"""File -> text segments. Add a format with ``register_extractor(".ext", fn)``."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import io
from pathlib import PurePath
import re


class ExtractionError(ValueError):
    """The file could not be read as the format its extension claims."""


@dataclass(frozen=True)
class Segment:
    text: str
    locator: str  # human-readable position used in citations: "p.3", "slide 5", "00:12:30", "## 제목"


Extractor = Callable[[bytes], list[Segment]]
_REGISTRY: dict[str, Extractor] = {}


def register_extractor(extension: str, fn: Extractor) -> None:
    _REGISTRY[extension.lower()] = fn


def supported_extensions() -> list[str]:
    return sorted(_REGISTRY)


def extract(filename: str, data: bytes) -> list[Segment]:
    ext = PurePath(filename).suffix.lower()
    if ext not in _REGISTRY:
        raise ExtractionError(f"지원하지 않는 형식입니다: {ext or '(확장자 없음)'}")
    try:
        segments = _REGISTRY[ext](data)
    except ExtractionError:
        raise
    except Exception as exc:  # parser libraries raise many unrelated types on corrupt files
        raise ExtractionError(f"파일을 읽을 수 없습니다 ({type(exc).__name__}).") from exc
    segments = [s for s in segments if s.text.strip()]
    if not segments:
        raise ExtractionError("파일에서 읽을 수 있는 텍스트를 찾지 못했습니다. (스캔 이미지 PDF는 지원하지 않습니다)")
    return segments


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# --- built-in extractors ---------------------------------------------------
def _text(data: bytes) -> list[Segment]:
    return [Segment(_decode(data), "전체")]


_HEADING = re.compile(r"(?m)^(#{1,3})\s+(.+?)\s*$")


def _markdown(data: bytes) -> list[Segment]:
    text = _decode(data)
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [Segment(text, "전체")]
    segments: list[Segment] = []
    if text[: matches[0].start()].strip():
        segments.append(Segment(text[: matches[0].start()], "머리말"))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segments.append(Segment(text[match.end():end], f"{match.group(1)} {match.group(2)}"))
    return segments


def _pdf(data: bytes) -> list[Segment]:
    import logging

    from pypdf import PdfReader  # imported lazily: optional at import time, required when used

    logging.getLogger("pypdf").setLevel(logging.CRITICAL)  # corrupt files are reported via ExtractionError

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ExtractionError("암호가 걸린 PDF는 읽을 수 없습니다.")
    return [Segment(page.extract_text() or "", f"p.{number}") for number, page in enumerate(reader.pages, start=1)]


def _pptx(data: bytes) -> list[Segment]:
    from pptx import Presentation

    segments: list[Segment] = []
    for number, slide in enumerate(Presentation(io.BytesIO(data)).slides, start=1):
        parts = [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame and shape.text_frame.text.strip()]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"[발표자 노트] {notes}")
        segments.append(Segment("\n\n".join(parts), f"slide {number}"))
    return segments


_TIMESTAMP = re.compile(r"(\d{1,2}:\d{2}:\d{2})[.,]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}")
_CUES_PER_SEGMENT = 12


def _subtitles(data: bytes) -> list[Segment]:
    """SRT/VTT: drop numbering and timing lines, group cues so citations point to a timestamp."""
    cues: list[tuple[str, str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", _decode(data)):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        stamp = ""
        body: list[str] = []
        for line in lines:
            match = _TIMESTAMP.search(line)
            if match:
                stamp = match.group(1)
            elif not (line.isdigit() or line.upper().startswith(("WEBVTT", "NOTE"))):
                body.append(line)
        if body:
            cues.append((stamp, " ".join(body)))
    segments: list[Segment] = []
    for start in range(0, len(cues), _CUES_PER_SEGMENT):
        group = cues[start:start + _CUES_PER_SEGMENT]
        segments.append(Segment("\n".join(text for _, text in group), group[0][0] or f"cue {start + 1}"))
    return segments


register_extractor(".txt", _text)
register_extractor(".md", _markdown)
register_extractor(".markdown", _markdown)
register_extractor(".pdf", _pdf)
register_extractor(".pptx", _pptx)
register_extractor(".srt", _subtitles)
register_extractor(".vtt", _subtitles)

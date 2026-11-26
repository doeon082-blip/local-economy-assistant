"""HTTP API. The UI (Streamlit today, React later) talks to this and nothing else.

Security defaults mirror economy_assistant: loopback-only. When ``ALLOW_REMOTE_ACCESS=true`` every
/api request must carry ``X-API-Key`` matching ``CLASS_API_KEY`` (fail closed if the key is unset).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
import json
import os
import secrets
import threading
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import config
from .features import FEATURES, ChatFeature, Feature, build_features
from .features.quiz import NoMaterialError, QuizFeature
from .features.progress import ProgressFeature
from .features.summary import SummaryFeature
from .ingest import supported_extensions
from .llm.base import LLMError
from .services import Services, UploadError, build_default_services

_UPLOAD_STATUS = {"empty": 400, "unsupported": 415, "too_large": 413, "unreadable": 422}
_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]  # no 'system': clients must not inject instructions
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    mode: str = Field(default="qa", max_length=32)
    doc_ids: list[int] | None = Field(default=None, max_length=50)
    level: int = Field(default=1, ge=1, le=3)  # used by the 'code' mode


class QuizGenerateRequest(BaseModel):
    doc_ids: list[int] | None = Field(default=None, max_length=50)
    count: int = Field(default=5, ge=1, le=10)
    kind: Literal["mcq", "short"] = "mcq"


class AnswerRequest(BaseModel):
    answer: str = Field(max_length=2000)


@contextmanager
def _http_errors():
    try:
        yield
    except UploadError as exc:
        raise HTTPException(status_code=_UPLOAD_STATUS.get(exc.reason, 400), detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:  # NoMaterialError and request-level validation failures
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def create_app(services: Services | None = None) -> FastAPI:
    """App factory. Pass ``services`` in tests; production builds them lazily on first use."""
    build_lock = threading.Lock()

    def ctx() -> tuple[Services, dict[str, Feature]]:
        with build_lock:  # the warm-up thread and the first request must not build services twice
            if app.state.services is None:
                app.state.services = build_default_services()
            if app.state.features is None:
                app.state.features = build_features(app.state.services)
        return app.state.services, app.state.features

    def warm_up() -> None:
        try:
            svc, _ = ctx()
            warm = getattr(svc.llm, "warmup", None)
            if callable(warm):
                warm()
        except Exception:
            pass  # warm-up is an optimisation; real requests report errors properly

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if services is None:  # production only: tests inject fakes and must not touch Ollama
            threading.Thread(target=warm_up, name="warmup", daemon=True).start()
        yield

    app = FastAPI(title="수업 도우미 API", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.services = services
    app.state.features = None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins(),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    @app.middleware("http")
    async def access_guard(request: Request, call_next):
        if request.url.path != "/healthz":
            if config.allow_remote_access():
                expected = os.environ.get("CLASS_API_KEY", "").strip()
                supplied = request.headers.get("X-API-Key", "")
                ok = bool(expected) and secrets.compare_digest(supplied.encode(), expected.encode())
                if not ok:
                    return JSONResponse(status_code=401 if expected else 403, content={"detail": "인증이 필요합니다. (CLASS_API_KEY)"})
            elif (request.client.host if request.client else "") not in _LOCAL_CLIENTS:
                return JSONResponse(status_code=403, content={"detail": "이 앱은 이 기기에서만 사용할 수 있습니다."})
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    # --- meta --------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/status")
    def status():
        with _http_errors():
            svc, _ = ctx()
            return {
                "llm": svc.llm.health(),
                "counts": svc.db.stats(),
                "vectors": svc.index.count(),
                "extensions": supported_extensions(),
                "max_upload_mb": config.max_upload_bytes() // (1024 * 1024),
            }

    @app.get("/api/features")
    def features():
        return {"features": [cls.info() for cls in FEATURES.values()]}

    # --- documents ---------------------------------------------------------
    @app.post("/api/documents")
    async def upload_document(file: UploadFile = File(...)):
        limit = config.max_upload_bytes()
        data = await file.read(limit + 1)  # never buffer more than limit+1 bytes
        with _http_errors():
            svc, _ = ctx()
            document, created = await run_in_threadpool(svc.library.add_file, file.filename or "untitled", data)
        return JSONResponse(status_code=201 if created else 200, content={"document": document, "created": created})

    @app.get("/api/documents")
    def list_documents():
        svc, _ = ctx()
        return {"documents": svc.db.list_documents()}

    @app.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: int):
        svc, _ = ctx()
        if not svc.library.remove(doc_id):
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
        return {"deleted": doc_id}

    @app.post("/api/documents/{doc_id}/summary")
    def summarize(doc_id: int, refresh: bool = Query(default=False)):
        with _http_errors():
            _, feats = ctx()
            result = feats["summary"].summarize(doc_id, refresh=refresh)  # type: ignore[attr-defined]
        if result is None:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
        return result

    # --- chat (any registered ChatFeature is selectable via `mode`) -----------
    @app.post("/api/chat/stream")
    def chat_stream(request: ChatRequest):
        with _http_errors():
            _, feats = ctx()
            feature = feats.get(request.mode)
            if not isinstance(feature, ChatFeature):
                modes = sorted(n for n, f in feats.items() if isinstance(f, ChatFeature))
                raise ValueError(f"지원하지 않는 mode입니다: {request.mode} (가능: {', '.join(modes)})")
            history = [m.model_dump() for m in request.history]
            meta, tokens = feature.start(request.question.strip(), history, doc_ids=request.doc_ids, level=request.level)

        def events() -> Iterator[str]:
            yield json.dumps({"type": "meta", **meta}, ensure_ascii=False) + "\n"
            try:
                for token in tokens:
                    yield json.dumps({"type": "delta", "content": token}, ensure_ascii=False) + "\n"
                yield json.dumps({"type": "done"}, ensure_ascii=False) + "\n"
            except (LLMError, ValueError) as exc:
                yield json.dumps({"type": "error", "detail": str(exc)}, ensure_ascii=False) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})

    # --- quiz / progress ---------------------------------------------------
    @app.post("/api/quiz/generate")
    def quiz_generate(request: QuizGenerateRequest):
        with _http_errors():
            _, feats = ctx()
            quiz: QuizFeature = feats["quiz"]  # type: ignore[assignment]
            return quiz.generate(request.doc_ids, request.count, request.kind)

    @app.get("/api/quiz/review")
    def quiz_review(limit: int = Query(default=5, ge=1, le=20)):
        _, feats = ctx()
        quiz: QuizFeature = feats["quiz"]  # type: ignore[assignment]
        return {"items": quiz.review(limit)}

    @app.post("/api/quiz/{quiz_id}/answer")
    def quiz_answer(quiz_id: int, request: AnswerRequest):
        with _http_errors():
            _, feats = ctx()
            quiz: QuizFeature = feats["quiz"]  # type: ignore[assignment]
            result = quiz.grade(quiz_id, request.answer)
        if result is None:
            raise HTTPException(status_code=404, detail="문제를 찾을 수 없습니다.")
        return result

    @app.get("/api/progress")
    def progress() -> dict[str, Any]:
        _, feats = ctx()
        report: ProgressFeature = feats["progress"]  # type: ignore[assignment]
        return report.report()

    return app


app = create_app()

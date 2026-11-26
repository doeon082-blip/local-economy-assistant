"""FastAPI backend; supports local loopback mode, streaming chat, and batch ECOS sync."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
import json
from pathlib import Path
import secrets
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
    allow_remote_access,
    app_host,
    app_port,
    cors_origins,
    ecos_api_key,
    model_name,
    rate_limit_per_minute,
    sync_admin_key,
)
from .data.ecos_client import EcosError, MAX_SYNC_LIMIT, fetch_many, fetch_series
from .data.sqlite_store import StatisticsDatabase
from .data.vector_store import VectorStore
from .security_policy import ActionRequest, RateLimiter, SecurityPolicy, require_allowed, tokens_match
from .services.economic_assistant import answer, answer_stream
from .services.ollama_client import OllamaError, status as ollama_status

_store: VectorStore | None = None
_db: StatisticsDatabase | None = None
approval_token = secrets.token_urlsafe(32)
policy = SecurityPolicy(
    allowed_tools=frozenset({"local_ollama_chat", "ecos_sync"}),
    approval_required_tools=frozenset({"ecos_sync"}),
    allow_external_sends=True,
    allow_data_changes=True,
)
MAX_HISTORY_CHARS = 24000
MAX_AUTH_FAILURES_PER_MINUTE = 10
RATE_LIMITED_PREFIXES = ("/api/chat", "/api/data/sync")
_request_limiter = RateLimiter()
_auth_failure_limiter = RateLimiter()
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def get_db() -> StatisticsDatabase:
    global _db
    if _db is None:
        _db = StatisticsDatabase()
    return _db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_db()
    get_store()
    yield


app = FastAPI(title="로컬 경제 연구실 API", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Local-Approval"],
)

if (frontend_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="frontend-assets")


class ChatMessage(BaseModel):
    # system/tool 역할은 받지 않습니다: 클라이언트가 시스템 지시문을 주입하지 못하게 합니다.
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)

    def history_dicts(self) -> list[dict[str, str]]:
        return [message.model_dump() for message in self.history]


class SyncRequest(BaseModel):
    stat_code: str = Field(default="722Y001", max_length=32)
    item_code: str = Field(default="0101000", max_length=32)
    cycle: str = Field(default="D", max_length=2)
    start: str = Field(default="20200101", max_length=8)
    end: str = Field(default_factory=lambda: datetime.now().strftime("%Y%m%d"), max_length=8)
    limit: int = Field(default=5000, ge=1, le=MAX_SYNC_LIMIT)


class BatchSyncRequest(BaseModel):
    items: list[SyncRequest] = Field(min_length=1, max_length=12)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_sync_approval(request: Request) -> None:
    admin_key = sync_admin_key()
    if allow_remote_access() and not admin_key:
        # 외부 공개 모드에서 관리자 키가 없으면 누구나 동기화할 수 있으므로 기능 자체를 닫습니다.
        raise PermissionError("외부 공개 모드에서는 SYNC_ADMIN_KEY를 설정해야 ECOS 동기화를 사용할 수 있습니다.")
    client = _client_key(request)
    if _auth_failure_limiter.is_blocked(client, MAX_AUTH_FAILURES_PER_MINUTE):
        raise HTTPException(status_code=429, detail="인증 실패가 너무 많습니다. 잠시 후 다시 시도하세요.", headers={"Retry-After": "60"})
    approved = tokens_match(request.headers.get("X-Local-Approval", ""), admin_key or approval_token)
    if not approved:
        _auth_failure_limiter.record(client)
    require_allowed(
        ActionRequest("ecos_sync", sends_data_outside=True, changes_data=True),
        policy,
        trusted_user_approved=approved,
    )


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    limit = rate_limit_per_minute()
    if limit and request.method == "POST" and request.url.path.startswith(RATE_LIMITED_PREFIXES):
        if not _request_limiter.allow(_client_key(request), limit):
            return JSONResponse(
                status_code=429,
                content={"detail": "요청이 너무 많습니다. 잠시 후 다시 시도하세요."},
                headers={"Retry-After": "60"},
            )
    return await call_next(request)


@app.middleware("http")
async def loopback_only(request: Request, call_next):
    if allow_remote_access() or request.url.path == "/healthz":
        return await call_next(request)
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return JSONResponse(status_code=403, content={"detail": "이 앱은 이 기기에서만 사용할 수 있습니다."})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/api/status")
def status():
    admin_key = sync_admin_key()
    db_stats = get_db().stats()
    payload = {
        "model": ollama_status(),
        "vector_count": get_store().stats()["observations"],
        "db_count": db_stats["observations"],
        "sync_runs": db_stats["sync_runs"],
        "max_sync_limit": MAX_SYNC_LIMIT,
        "ecos_key_configured": bool(ecos_api_key()),
        # 외부 공개 모드에서는 키 입력이 필수이고, 승인 토큰은 절대 내려주지 않습니다.
        "sync_requires_key": bool(admin_key) or allow_remote_access(),
        "sync_enabled": bool(admin_key) or not allow_remote_access(),
        "approval_token": None if (admin_key or allow_remote_access()) else approval_token,
    }
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


@app.get("/api/data/observations")
def list_observations(stat_code: str = Query(default="", max_length=32), limit: int = Query(default=100, ge=1, le=500)):
    rows = get_db().list_observations(stat_code=stat_code, limit=limit)
    return {"count": len(rows), "observations": rows}


def _validated_question(request: ChatRequest) -> str:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="질문을 입력해 주세요.")
    if sum(len(item.content) for item in request.history) > MAX_HISTORY_CHARS:
        raise HTTPException(status_code=422, detail="대화 이력이 너무 깁니다. 새 대화를 시작해 주세요.")
    require_allowed(ActionRequest("local_ollama_chat"), policy)
    return question


@contextmanager
def _chat_errors():
    try:
        yield
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (OllamaError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/chat")
def chat(request: ChatRequest):
    with _chat_errors():
        question = _validated_question(request)
        return answer(question, request.history_dicts(), get_store(), get_db())


def _stream_ndjson(citations: list[dict[str, str]], token_iter):
    yield json.dumps({"type": "meta", "model": model_name(), "citations": citations}, ensure_ascii=False) + "\n"
    try:
        for token in token_iter:
            yield json.dumps({"type": "delta", "content": token}, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "done"}, ensure_ascii=False) + "\n"
    except (OllamaError, RuntimeError, ValueError) as exc:
        yield json.dumps({"type": "error", "detail": str(exc)}, ensure_ascii=False) + "\n"


@app.post("/api/chat/stream")
def chat_stream_endpoint(request: ChatRequest):
    with _chat_errors():
        question = _validated_question(request)
        citations, token_iter = answer_stream(question, request.history_dicts(), get_store(), get_db())

    return StreamingResponse(
        _stream_ndjson(citations, token_iter),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/data/sync")
def sync_data(payload: SyncRequest, request: Request):
    try:
        _check_sync_approval(request)
        rows, _request_url = fetch_series(
            payload.stat_code,
            payload.cycle,
            payload.start,
            payload.end,
            payload.item_code,
            payload.limit,
        )
        db_saved = get_db().save_observations(
            rows,
            "https://ecos.bok.or.kr/api/",
            stat_code=payload.stat_code,
            item_code=payload.item_code,
            cycle=payload.cycle,
            start_period=payload.start,
            end_period=payload.end,
        )
        vector_saved = get_store().upsert(rows, "https://ecos.bok.or.kr/api/")
        return {
            "saved": vector_saved,
            "db_saved": db_saved,
            "db_count": get_db().stats()["observations"],
            "vector_count": get_store().stats()["observations"],
            "source": "한국은행 ECOS",
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EcosError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/data/sync/batch")
def sync_batch(payload: BatchSyncRequest, request: Request):
    try:
        _check_sync_approval(request)
        specs = [item.model_dump() for item in payload.items]
        fetched = fetch_many(specs)
        all_rows: list[dict[str, str]] = []
        db_saved = 0
        db = get_db()
        for spec, rows in fetched:
            db_saved += db.save_observations(
                rows,
                "https://ecos.bok.or.kr/api/",
                stat_code=str(spec["stat_code"]),
                item_code=str(spec["item_code"]),
                cycle=str(spec["cycle"]),
                start_period=str(spec["start"]),
                end_period=str(spec["end"]),
            )
            all_rows.extend(rows)
        vector_saved = get_store().upsert(all_rows, "https://ecos.bok.or.kr/api/")
        return {
            "series_count": len(fetched),
            "saved": vector_saved,
            "db_saved": db_saved,
            "db_count": db.stats()["observations"],
            "vector_count": get_store().stats()["observations"],
            "source": "한국은행 ECOS",
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EcosError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/")
def frontend():
    index = frontend_dist / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"message": "React UI를 실행하세요: cd frontend; npm run dev", "api": "/api/status"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("economy_assistant.api:app", host=app_host(), port=app_port(), reload=False)

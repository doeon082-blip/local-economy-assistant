"""FastAPI backend; supports local loopback mode, streaming chat, and batch ECOS sync."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
import json
from pathlib import Path
import secrets

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
    sync_admin_key,
)
from .data.ecos_client import EcosError, MAX_SYNC_LIMIT, fetch_many, fetch_series
from .data.sqlite_store import StatisticsDatabase
from .data.vector_store import VectorStore
from .security_policy import ActionRequest, SecurityPolicy, require_allowed
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
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"


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


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)


class SyncRequest(BaseModel):
    stat_code: str = Field(default="722Y001", max_length=32)
    item_code: str = Field(default="0101000", max_length=32)
    cycle: str = Field(default="D", max_length=2)
    start: str = Field(default="20200101", max_length=8)
    end: str = Field(default_factory=lambda: datetime.now().strftime("%Y%m%d"), max_length=8)
    limit: int = Field(default=5000, ge=1, le=MAX_SYNC_LIMIT)


class BatchSyncRequest(BaseModel):
    items: list[SyncRequest] = Field(min_length=1, max_length=12)


def _check_sync_approval(request: Request) -> None:
    token = request.headers.get("X-Local-Approval", "")
    expected_token = sync_admin_key() or approval_token
    approved = bool(token) and secrets.compare_digest(token, expected_token)
    require_allowed(
        ActionRequest("ecos_sync", sends_data_outside=True, changes_data=True),
        policy,
        trusted_user_approved=approved,
    )


@app.middleware("http")
async def loopback_only(request: Request, call_next):
    if allow_remote_access() or request.url.path == "/healthz":
        return await call_next(request)
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return JSONResponse(status_code=403, content={"detail": "이 앱은 이 기기에서만 사용할 수 있습니다."})
    return await call_next(request)


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
        "sync_requires_key": bool(admin_key),
        "approval_token": None if admin_key else approval_token,
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
    if sum(len(item.get("content", "")) for item in request.history) > MAX_HISTORY_CHARS:
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
        return answer(question, request.history, get_store(), get_db())


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
        citations, token_iter = answer_stream(question, request.history, get_store(), get_db())

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

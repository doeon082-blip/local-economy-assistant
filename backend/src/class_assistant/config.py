"""Settings for the class assistant. Every value can be overridden through ``.env``."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

# backend/src/class_assistant/config.py -> repository root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / ".local_data" / "class_assistant"
DB_PATH = DATA_DIR / "class_assistant.sqlite3"
VECTOR_DIR = DATA_DIR / "vector_store"

_TRUE = {"1", "true", "yes", "on"}


def _read_local_env() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip("\"'")
        if name and name not in os.environ:
            os.environ[name] = value


_read_local_env()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(_env(name, str(default)))))
    except ValueError:
        return default


def allow_remote_access() -> bool:
    return _env("ALLOW_REMOTE_ACCESS").lower() in _TRUE


def app_host() -> str:
    return _env("CLASS_HOST", "0.0.0.0" if allow_remote_access() else "127.0.0.1")


def app_port() -> int:
    return _env_int("CLASS_PORT", 8800, 1, 65535)


def cors_origins() -> list[str]:
    raw = _env(
        "CLASS_CORS_ORIGINS",
        "http://127.0.0.1:8501,http://localhost:8501,http://127.0.0.1:5173,http://localhost:5173",
    )
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


# --- LLM -----------------------------------------------------------------
def llm_provider() -> str:
    """Name of a provider registered in ``class_assistant.llm`` (default: ollama)."""
    return _env("LLM_PROVIDER", "ollama").lower()


def ollama_url() -> str:
    value = _env("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urlsplit(value)
    remote_ok = _env("ALLOW_REMOTE_OLLAMA").lower() in _TRUE
    if parsed.scheme not in ({"http", "https"} if remote_ok else {"http"}) or not parsed.hostname:
        raise ValueError("OLLAMA_URL 형식이 올바르지 않습니다.")
    if not remote_ok and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("OLLAMA_URL은 로컬 주소만 허용합니다. (원격 허용 시 ALLOW_REMOTE_OLLAMA=true)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OLLAMA_URL에 계정 정보나 추가 매개변수를 넣을 수 없습니다.")
    return value


def chat_model() -> str:
    return _env("CLASS_LLM_MODEL") or _env("OLLAMA_MODEL", "qwen3.5:4b")


def embed_model() -> str:
    return _env("EMBED_MODEL", "nomic-embed-text")


def ollama_num_ctx() -> int:
    return _env_int("OLLAMA_NUM_CTX", 4096, 1024, 32768)


def ollama_keep_alive() -> str:
    """How long Ollama keeps the model in memory after a call. Ollama's default (5 min) makes the
    first request after a short break pay a full model reload, which is the main source of latency."""
    return _env("OLLAMA_KEEP_ALIVE", "30m")


def ollama_num_predict() -> int:
    return _env_int("OLLAMA_NUM_PREDICT", 1024, 128, 8192)


# --- Uploads -------------------------------------------------------------
def max_upload_bytes() -> int:
    return _env_int("MAX_UPLOAD_MB", 25, 1, 500) * 1024 * 1024

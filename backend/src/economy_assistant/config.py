"""Application settings supporting both local-only mode and containerized deployment."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

# backend/src/economy_assistant/config.py → 저장소 루트(.env, .local_data 위치)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DATA_DIR = PROJECT_ROOT / ".local_data"
VECTOR_DIR = LOCAL_DATA_DIR / "vector_store"
DB_PATH = LOCAL_DATA_DIR / "ecos_statistics.sqlite3"
DEFAULT_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
APP_HOST = "127.0.0.1"
APP_PORT = 8765
ECOS_API_ROOT = "https://ecos.bok.or.kr/api"
DEFAULT_CORS_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")


def _read_local_env() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip('"\'')
        if name and name not in os.environ:
            os.environ[name] = value


_read_local_env()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def allow_remote_access() -> bool:
    return _env_bool("ALLOW_REMOTE_ACCESS", False)


def allow_remote_ollama() -> bool:
    return _env_bool("ALLOW_REMOTE_OLLAMA", False)


def app_host() -> str:
    default = "0.0.0.0" if allow_remote_access() else APP_HOST
    return (os.environ.get("HOST") or os.environ.get("APP_HOST") or default).strip() or default


def app_port() -> int:
    raw = (os.environ.get("PORT") or os.environ.get("APP_PORT") or str(APP_PORT)).strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("PORT 값이 올바른 정수가 아닙니다.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT 범위는 1~65535여야 합니다.")
    return port


def cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "")
    if raw.strip():
        return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    return list(DEFAULT_CORS_ORIGINS)


def ollama_url() -> str:
    value = os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL).strip().rstrip("/")
    parsed = urlsplit(value)
    allowed_schemes = {"http", "https"} if allow_remote_ollama() else {"http"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise ValueError("OLLAMA_URL 형식이 올바르지 않습니다.")
    if not allow_remote_ollama() and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("OLLAMA_URL은 로컬 주소만 허용합니다. (원격 허용 시 ALLOW_REMOTE_OLLAMA=true)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OLLAMA_URL에 계정 정보나 추가 URL 매개변수를 넣을 수 없습니다.")
    return value


def model_name() -> str:
    return os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def ollama_num_ctx() -> int:
    return _env_int("OLLAMA_NUM_CTX", 4096, 1024, 32768)


def ollama_num_predict() -> int:
    return _env_int("OLLAMA_NUM_PREDICT", 768, 128, 4096)


def ollama_keep_alive() -> str:
    return os.environ.get("OLLAMA_KEEP_ALIVE", "30m").strip() or "30m"


def embed_batch_size() -> int:
    return _env_int("EMBED_BATCH_SIZE", 128, 16, 512)


def embed_workers() -> int:
    return _env_int("EMBED_WORKERS", 3, 1, 8)


def ecos_api_key() -> str:
    return os.environ.get("ECOS_API_KEY", "").strip()


def sync_admin_key() -> str:
    return os.environ.get("SYNC_ADMIN_KEY", "").strip()

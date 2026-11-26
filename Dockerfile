FROM node:24-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/vite.config.js ./
COPY frontend/src ./src
RUN npm run build

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/.local_data/vector_store \
    && chown -R appuser:appuser /app/.local_data

USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    ALLOW_REMOTE_ACCESS=true \
    ALLOW_REMOTE_OLLAMA=true \
    HOST=0.0.0.0 \
    PORT=8765 \
    OLLAMA_URL=http://ollama:11434

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8765\")}/healthz', timeout=3)"

CMD ["sh", "-c", "uvicorn economy_assistant.api:app --host ${HOST:-0.0.0.0} --port ${PORT:-8765}"]

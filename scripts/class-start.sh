#!/usr/bin/env bash
# 수업 도우미: API(8800)와 임시 Streamlit 화면(8501)을 함께 실행합니다 (macOS/Linux). 종료: Ctrl+C
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --project backend class-assistant &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT
uv run --project backend --extra ui streamlit run frontend_streamlit/app.py

@echo off
rem 수업 도우미: API 서버(8800)와 임시 Streamlit 화면(8501)을 각각 새 창에서 실행합니다.
cd /d "%~dp0.."
start "class-assistant API" cmd /k uv run --project backend class-assistant
start "class-assistant UI" cmd /k uv run --project backend --extra ui streamlit run frontend_streamlit/app.py

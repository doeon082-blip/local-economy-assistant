"""수업 도우미: 업로드한 수업 자료로 Q&A, 퀴즈, 요약, 코드 도우미를 제공하는 로컬 우선 AI 앱."""
from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="수업 도우미 API 서버")
    parser.add_argument("--host", help="바인딩 주소 (기본: 127.0.0.1, CLASS_HOST)")
    parser.add_argument("--port", type=int, help="포트 (기본: 8800, CLASS_PORT)")
    parser.add_argument("--reload", action="store_true", help="코드 변경 시 자동 재시작 (개발용)")
    args = parser.parse_args(argv)

    import uvicorn

    from . import config

    uvicorn.run(
        "class_assistant.api:app",
        host=args.host or config.app_host(),
        port=args.port or config.app_port(),
        reload=args.reload,
    )

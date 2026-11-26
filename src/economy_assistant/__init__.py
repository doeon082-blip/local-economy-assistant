"""로컬 전용 한국은행 경제 데이터 대화 도구."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys


def _find_node_executable(frontend_dir: Path) -> str | None:
    global_node = shutil.which("node")
    if global_node:
        return global_node
    for candidate in (
        Path(r"C:\Program Files\nodejs\node.exe"),
        frontend_dir / ".local_tools" / "node" / "node-v24.22.3-win-x64" / "node.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _run_dev(host: str, port: int) -> None:
    project_root = Path(__file__).resolve().parents[2]
    frontend_dir = project_root / "frontend"
    vite_entry = frontend_dir / "node_modules" / "vite" / "bin" / "vite.js"
    node_exe = _find_node_executable(frontend_dir)

    if not node_exe or not vite_entry.is_file():
        raise RuntimeError(
            "Node.js 또는 frontend/node_modules/vite를 찾을 수 없습니다. "
            "`cd frontend && npm.cmd install` 후 다시 시도하세요."
        )

    backend_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "economy_assistant.api:app",
        "--host",
        host,
        "--port",
        str(port),
        "--reload",
    ]
    frontend_cmd = [node_exe, str(vite_entry), "--host", "127.0.0.1"]

    print(f"[경제 연구실] 통합 개발 모드 시작")
    print(f"  - React 화면 : http://127.0.0.1:5173 (API 자동 프록시)")
    print(f"  - FastAPI 서버: http://{host}:{port}")

    backend_proc = subprocess.Popen(backend_cmd, cwd=project_root)
    frontend_env = os.environ.copy()
    frontend_env["VITE_BACKEND_URL"] = f"http://127.0.0.1:{port}"
    frontend_proc = subprocess.Popen(frontend_cmd, cwd=frontend_dir, env=frontend_env)
    procs = [backend_proc, frontend_proc]

    def _shutdown(*_args) -> None:
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            for proc in procs:
                code = proc.poll()
                if code is not None:
                    _shutdown()
                    for other in procs:
                        if other.poll() is None:
                            other.wait(timeout=5)
                    if code not in (0, -signal.SIGINT):
                        sys.exit(code)
                    return
            try:
                backend_proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        _shutdown()
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="로컬 경제 연구실 실행기")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="FastAPI 백엔드(--reload)와 React(Vite) 개발 서버를 한 번에 함께 실행합니다.",
    )
    args = parser.parse_args(argv)

    from .config import app_host, app_port

    host = app_host()
    port = app_port()
    if args.dev:
        _run_dev(host, port)
        return

    import uvicorn

    uvicorn.run("economy_assistant.api:app", host=host, port=port, reload=False)

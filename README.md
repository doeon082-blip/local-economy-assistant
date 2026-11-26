# 로컬 경제 연구실

Ollama의 Qwen3.5 4B 모델과 한국은행 ECOS 경제통계를 사용하는 한국어 경제 대화 앱입니다. FastAPI 백엔드, React 화면, 로컬 Chroma 벡터 DB로 구성됩니다.

## 개인정보 및 연결 범위

- 기본 설정(`ALLOW_REMOTE_ACCESS=false`, `ALLOW_REMOTE_OLLAMA=false`)에서는 채팅 질문을 `127.0.0.1`의 Ollama에만 전달하며 앱 서버도 루프백 주소에만 바인딩합니다.
- 앱은 대화 이력을 파일이나 DB에 저장하지 않습니다. 대화는 현재 브라우저 탭의 메모리에만 있습니다.
- ECOS는 사용자가 **ECOS 데이터 가져오기**를 누를 때만 요청하며, 동기화에는 통계 코드와 기간만 포함됩니다.
- 통계 원자료와 동기화 이력은 `.local_data/ecos_statistics.sqlite3` SQLite DB에, 검색 임베딩은 `.local_data/vector_store/` Chroma DB에 즉시 저장되며 모두 Git에서 제외됩니다. Chroma 익명 사용 통계는 꺼 둡니다.

## 로컬에서 시작하기

1. Python 3.13 이상, Node.js 24 LTS 이상, Ollama를 설치합니다.
2. 모델을 내려받습니다 (Qwen3.5 4B 파일 약 3.3~4.0GB).

   ```powershell
   ollama pull qwen3.5:4b
   ollama pull nomic-embed-text
   ```

3. Python 패키지를 설치하고 **통합 실행**(하나의 명령어로 백엔드 + 화면 연계)합니다.
   - **단일 통합 서버 실행** (빌드된 React 화면까지 `http://127.0.0.1:8765`에서 바로 제공):

     ```powershell
     uv sync
     uv run python src/main.py
     ```
     *(또는 `.\start.cmd` 실행)*

   - **통합 개발 모드 실행** (한 번에 FastAPI `--reload` + React Vite 개발 서버 `http://127.0.0.1:5173` 동시 실행 및 API 자동 프록시):

     ```powershell
     uv run python src/main.py --dev
     ```
     *(또는 `.\dev.cmd` 실행)*

## Docker / 서버 배포

표준 멀티스테이지 `Dockerfile`과 `docker-compose.yml`이 포함되어 있습니다.

1. 환경 변수 파일을 준비합니다.

   ```powershell
   Copy-Item .env.example .env
   ```

   외부 공개 서버에 배포할 때는 `.env`에 `SYNC_ADMIN_KEY`를 설정해 임의 사용자가 서버의 ECOS 동기화를 호출하지 못하도록 보호하는 것을 권장합니다.

2. Docker Compose로 앱 + Ollama를 함께 실행합니다.

   ```powershell
   docker compose up -d --build
   ```

   - 첫 실행 시 `ollama-init` 서비스가 `qwen3.5:4b`와 `nomic-embed-text`를 자동으로 내려받습니다.
   - 완료 후 `http://<서버-주소>:8765`로 접속할 수 있으며 헬스체크 경로는 `/healthz`입니다.
   - Cloud Run, Railway, Render(`render.yaml` 무료 플랜), Hugging Face Spaces(무료 Docker Space) 등 단일 컨테이너 PaaS에 `Dockerfile`을 배포할 때는 `ALLOW_REMOTE_ACCESS=true`, `ALLOW_REMOTE_OLLAMA=true`, `OLLAMA_URL`, `PORT` 환경 변수를 플랫폼에 맞게 지정하세요.
   - 프론트엔드만 무료 정적 호스팅(Netlify `netlify.toml` 등)에 올릴 경우 빌드 환경 변수 `VITE_API_BASE`에 백엔드 주소를 지정하세요.

## ECOS 설정

[한국은행 ECOS Open API](https://ecos.bok.or.kr/api/)에서 키를 발급받고 프로젝트 루트의 `.env`에 입력할 수 있습니다. `.env`는 Git에서 제외됩니다.

```dotenv
ECOS_API_KEY=발급받은_개인_API_키
```

키가 없어도 ECOS 샘플 키로 최대 10건을 가져올 수 있습니다. 개인 API 키를 입력하면 1,000건 단위 자동 페이지네이션으로 지표당 최대 50,000건까지 가져오며, **주요 4대 지표 한 번에 받기**로 기준금리(`722Y001`)·환율(`731Y001`)·소비자물가(`901Y009`)·GDP(`200Y001`)를 병렬 수집해 SQLite DB와 Chroma 벡터 DB에 즉시 저장할 수 있습니다. (이미 저장·임베딩된 동일 관측치는 재임베딩을 건너뜁니다.)

## 테스트

```powershell
uv run python -m unittest discover -s tests -v
```

## 구조

```text
├── Dockerfile                 # Node 24 + Python 3.13 (uv) 멀티스테이지 빌드
├── docker-compose.yml         # 앱 + Ollama + 모델 초기화 구성
├── .env.example               # 로컬/배포 환경 변수 템플릿
├── .github/workflows/ci.yml   # 백엔드 테스트, 프론트 빌드, Docker 빌드 검증
├── src/economy_assistant/
│   ├── config.py              # 로컬/원격 실행, 모델 및 API 키 설정
│   ├── api.py                 # FastAPI 서버 및 정적 프론트엔드 서빙
│   ├── security_policy.py     # API 작업 허용 및 동기화 승인 검사
│   ├── data/
│   │   ├── ecos_client.py     # ECOS 데이터 동기화 및 검증
│   │   ├── sqlite_store.py    # ECOS 원자료 및 동기화 이력 즉시 SQLite 저장
│   │   └── vector_store.py    # Chroma DB 및 Ollama 임베딩
│   └── services/
│       ├── ollama_client.py   # Ollama 연결 및 모델 상태 확인
│       └── economic_assistant.py
├── frontend/                  # React + Vite UI
└── tests/                     # 단위 및 API 통합 테스트
```

## 한계

4B 모델은 복잡한 경제 분석에 한계가 있습니다. 동기화되지 않은 최신 수치나 인과관계는 틀릴 수 있으므로 중요 수치는 ECOS 원자료를 확인하세요.

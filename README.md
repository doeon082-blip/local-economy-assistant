# 로컬 경제 연구실

[![CI](https://github.com/doeon082-blip/local-economy-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/doeon082-blip/local-economy-assistant/actions/workflows/ci.yml)

Ollama의 Qwen3.5 4B 모델과 한국은행 ECOS 경제통계를 사용하는 한국어 경제 대화 앱입니다. FastAPI 백엔드, React 화면, SQLite 원자료 저장소, 로컬 Chroma 벡터 DB로 구성됩니다. 질문은 기본적으로 내 PC의 Ollama에서만 처리됩니다.

## 목차

- [주요 기능](#주요-기능)
- [동작 방식](#동작-방식)
- [기술 스택](#기술-스택)
- [개인정보 및 연결 범위](#개인정보-및-연결-범위)
- [로컬에서 시작하기](#로컬에서-시작하기)
- [상세 문서](#상세-문서)
- [개발과 테스트](#개발과-테스트)
- [프로젝트 구조](#프로젝트-구조)
- [문제 해결](#문제-해결)
- [한계](#한계)

## 주요 기능

- **스트리밍 채팅**: 답변이 생성되는 대로 NDJSON으로 전달되며, 답변마다 근거가 된 ECOS 통계(통계명·항목·기간·출처 URL)를 함께 보여 줍니다.
- **하이브리드 검색(RAG)**: 질문과 의미가 가까운 관측치는 Chroma 벡터 검색으로, 각 지표의 최신 값은 SQLite에서 가져와 프롬프트에 함께 넣습니다. 중복되는 관측치는 한 번만 넣습니다.
- **ECOS 데이터 가져오기**: 통계표 코드·항목 코드·주기(일/월/분기/연)·기간을 지정해 수집합니다. 개인 API 키가 있으면 1,000건 단위 자동 페이지네이션으로 지표당 최대 50,000건까지 받습니다.
- **주요 4대 지표 한 번에 받기**: 기준금리(`722Y001`), 환율(`731Y001`), 소비자물가(`901Y009`), GDP(`200Y001`)를 병렬로 수집합니다.
- **즉시 저장과 증분 임베딩**: 가져온 데이터는 곧바로 SQLite에 저장되고, 이미 임베딩한 동일 관측치는 다시 임베딩하지 않습니다.
- **반응형 UI**: 모바일 드로어 메뉴, 대화 스크롤 조절 슬라이더, Enter 전송·Shift+Enter 줄바꿈을 지원합니다.
- **보안 기본값**: 루프백 전용 바인딩, 동기화 승인 토큰, 프롬프트 주입 방어 문구를 기본으로 적용합니다.

## 동작 방식

```text
브라우저(React)
   │  POST /api/chat/stream
   ▼
FastAPI ──► 벡터 검색(Chroma, 상위 8건) ─┐
   │    └─► 지표별 최신값(SQLite, 4건) ──┤  중복 제거 후
   │                                    ▼
   └──────────────────────────► 프롬프트 구성(<untrusted_data>로 감싼 참고 자료)
                                         │
                                         ▼
                                  Ollama (qwen3.5:4b) ──► 토큰 스트리밍 응답

ECOS 가져오기:  브라우저 ─► POST /api/data/sync(/batch) ─► ECOS Open API
                                   ├─► SQLite 즉시 저장
                                   └─► nomic-embed-text 임베딩 ─► Chroma 저장
```

- ECOS 자료는 신뢰할 수 없는 데이터로 취급합니다. 시스템 프롬프트가 자료 안의 지시문을 실행하지 않도록 지시하며, 자료는 `<untrusted_data>` 태그로 감싸서 전달합니다.
- 대화 이력은 최근 8개 메시지만, 메시지당 4,000자까지만 모델에 전달합니다. 이력 전체가 24,000자를 넘으면 요청을 거절합니다.

## 기술 스택

| 영역 | 사용 기술 |
| --- | --- |
| 백엔드 | Python 3.13, FastAPI, Uvicorn, httpx |
| 프론트엔드 | React 19, Vite |
| 모델 | Ollama — 채팅 `qwen3.5:4b`, 임베딩 `nomic-embed-text` |
| 저장소 | SQLite(WAL 모드, 원자료·동기화 이력), Chroma(벡터) |
| 패키지 관리 | uv, npm |
| 배포 | Docker 멀티스테이지, Docker Compose, Render, Netlify |
| CI | GitHub Actions |

## 개인정보 및 연결 범위

- 기본 설정(`ALLOW_REMOTE_ACCESS=false`, `ALLOW_REMOTE_OLLAMA=false`)에서는 채팅 질문을 `127.0.0.1`의 Ollama에만 전달하며 앱 서버도 루프백 주소에만 바인딩합니다. 다른 기기에서 접근하면 403으로 거절합니다(`/healthz` 제외).
- 앱은 대화 이력을 파일이나 DB에 저장하지 않습니다. 대화는 현재 브라우저 탭의 메모리에만 있습니다.
- ECOS는 사용자가 **ECOS 데이터 가져오기**를 누를 때만 요청하며, 동기화에는 통계 코드와 기간만 포함됩니다. API 키는 로그와 오류 메시지에서 가려집니다.
- 통계 원자료와 동기화 이력은 `.local_data/ecos_statistics.sqlite3` SQLite DB에, 검색 임베딩은 `.local_data/vector_store/` Chroma DB에 즉시 저장되며 모두 Git에서 제외됩니다. Chroma 익명 사용 통계는 꺼 둡니다.
- ECOS 동기화는 승인이 필요한 작업입니다. `X-Local-Approval` 헤더가 없거나 틀리면 거절하고, 서버 공개 모드에서는 `SYNC_ADMIN_KEY`가 없으면 동기화를 아예 비활성화합니다.
- 요청 제한, 보안 헤더, 인증 실패 잠금 등 적용된 보안 장치와 그 이유는 [SECURITY.md](SECURITY.md)에 정리했습니다.

## 로컬에서 시작하기

### 준비물

- Python 3.13 이상, [uv](https://docs.astral.sh/uv/)
- Node.js 24 LTS 이상 (프론트엔드 빌드·개발 모드에 필요)
- [Ollama](https://ollama.com/)

### 1. 모델 내려받기

Qwen3.5 4B 파일은 약 3.3~4.0GB입니다.

```powershell
ollama pull qwen3.5:4b
ollama pull nomic-embed-text
```

### 2. 실행

- **단일 통합 서버** — 빌드된 React 화면까지 `http://127.0.0.1:8765`에서 바로 제공합니다.

  ```powershell
  uv sync --project backend
  uv run --project backend economy-assistant
  ```

  *(또는 `.\scripts\start.cmd` 실행)*

- **통합 개발 모드** — FastAPI `--reload`와 Vite 개발 서버(`http://127.0.0.1:5173`)를 한 번에 띄우고 API를 자동 프록시합니다.

  ```powershell
  cd frontend; npm install; cd ..
  uv run --project backend economy-assistant --dev
  ```

  *(또는 `.\scripts\dev.cmd` 실행)*

### 3. 처음 사용하기

1. 화면 왼쪽 아래 **ECOS 데이터** 카드를 눌러 가져오기 창을 엽니다.
2. **주요 4대 지표 한 번에 받기**를 누르면 금리·환율·물가·GDP가 저장됩니다.
3. 저장된 건수는 카드에 `DB n건 · 벡터 n건`으로 표시됩니다. 이제 "기준금리의 경제적 영향을 설명해줘" 같은 질문을 해 보세요.

키 없이도 동작하지만 지표당 10건만 가져옵니다. 많이 받으려면 [ECOS](https://ecos.bok.or.kr/api/)에서 키를 발급받아 저장소 루트의 `.env`에 `ECOS_API_KEY=키`를 넣으세요. 자세한 설정은 [docs/configuration.md](docs/configuration.md)를 참고하세요.

## 상세 문서

| 문서 | 내용 |
| --- | --- |
| [docs/configuration.md](docs/configuration.md) | ECOS 키, 주기별 기간 형식, 환경 변수 전체 표 |
| [docs/api.md](docs/api.md) | HTTP API 명세, 스트리밍 응답 형식, 오류 코드 |
| [docs/deployment.md](docs/deployment.md) | Docker Compose, Render, Netlify, GitHub Pages 배포 |
| [docs/class-assistant.md](docs/class-assistant.md) | **수업 도우미**(이 브랜치): 자료 Q&A·퀴즈·요약·코드 도우미 구조와 확장 방법 |
| [SECURITY.md](SECURITY.md) | 위협 모델, 보안 장치와 이유, 배포 체크리스트, 취약점 신고 |

## 개발과 테스트

```powershell
cd backend
uv sync
uv run python -m unittest discover -s tests -v
```

테스트 35개가 다음을 검증합니다. 외부 서비스(ECOS, Ollama)는 모의 객체로 대체되어 네트워크 없이 실행됩니다.

| 파일 | 대상 |
| --- | --- |
| `backend/tests/test_api.py` | 상태·채팅·스트리밍·동기화 API, 루프백 제한 |
| `backend/tests/test_config_and_policy.py` | 환경 설정, 보안 정책(fail-closed) |
| `backend/tests/test_ecos_and_vector_store.py` | ECOS 페이지네이션·키 가림, SQLite·증분 임베딩 |
| `backend/tests/test_assistant_and_db_queries.py` | 프롬프트 구성, SQLite 조회 |
| `backend/tests/test_ollama_client.py` | Ollama 일반·스트리밍 응답과 오류 처리 |
| `backend/tests/test_security.py` | 토큰 비교, 요청 제한, 인증 실패 잠금, 보안 헤더, 입력 검증 |

GitHub Actions는 `main` 푸시와 PR마다 백엔드 테스트 → 프론트엔드 빌드 → Docker 이미지 빌드 검증을 실행하고(`ci.yml`), `frontend/` 변경 시 GitHub Pages에 배포하며(`pages.yml`), 비밀키·의존성 취약점 검사는 푸시와 매주 월요일에 실행합니다(`security.yml`).

## 프로젝트 구조

```text
.
├── backend/                       # Python 백엔드 (FastAPI)
│   ├── pyproject.toml             #   의존성·패키지 설정 (uv)
│   ├── uv.lock
│   ├── src/class_assistant/       #   수업 도우미 백엔드 (docs/class-assistant.md)
│   ├── src/economy_assistant/
│   │   ├── __init__.py            #   실행기 (--dev 지원)
│   │   ├── api.py                 #   FastAPI 서버, 정적 프론트엔드 서빙
│   │   ├── config.py              #   환경 변수·경로 설정
│   │   ├── security_policy.py     #   작업 허용·동기화 승인 검사
│   │   ├── data/
│   │   │   ├── ecos_client.py     #   ECOS 수집·검증·페이지네이션
│   │   │   ├── sqlite_store.py    #   원자료·동기화 이력 SQLite 저장
│   │   │   └── vector_store.py    #   Chroma 벡터 DB, Ollama 임베딩
│   │   └── services/
│   │       ├── ollama_client.py   #   Ollama 연결·스트리밍·상태 확인
│   │       └── economic_assistant.py  # 하이브리드 검색·프롬프트 구성
│   └── tests/                     #   단위·API 통합 테스트
├── frontend/                      # React + Vite 화면
├── frontend_streamlit/            # 수업 도우미 임시 화면 (Streamlit, 곧 교체 예정)
│   ├── src/
│   └── package.json
├── scripts/                       # 실행 스크립트 (start / dev / frontend-dev)
├── docs/                          # 상세 문서 (설정·API·배포)
├── .github/
│   ├── workflows/                 # ci.yml, pages.yml, security.yml
│   └── dependabot.yml             # 의존성 자동 업데이트
├── SECURITY.md                    # 보안 정책·장치·체크리스트
├── Dockerfile                     # Node + Python 멀티스테이지 빌드
├── docker-compose.yml             # 앱 + Ollama + 모델 초기화
├── render.yaml                    # Render 백엔드 배포
├── netlify.toml                   # Netlify 프론트엔드 배포
├── .env.example                   # 환경 변수 템플릿 (실제 키는 .env에)
└── .local_data/                   # 실행 시 생성: SQLite·Chroma (Git 제외)
```

백엔드 코드는 `backend/src/economy_assistant/`에 있습니다. `.env`와 `.local_data/`는 저장소 루트에 둡니다.

## 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 화면 상단에 연결 안 됨 표시 | Ollama가 실행 중인지, `OLLAMA_URL`이 맞는지 확인 |
| 모델 또는 임베딩 모델 없음 | `ollama pull qwen3.5:4b`, `ollama pull nomic-embed-text` 실행 |
| 가져오기가 10건에서 멈춤 | `.env`에 개인 `ECOS_API_KEY`를 설정했는지 확인 |
| 동기화가 403으로 거절됨 | `SYNC_ADMIN_KEY`를 설정했다면 화면의 관리자 키 칸에 같은 값 입력 |
| 다른 기기에서 접속이 403 | 의도된 동작입니다. 서버 배포라면 `ALLOW_REMOTE_ACCESS=true`와 `HOST=0.0.0.0` 설정 |
| 화면이 안 보이고 JSON만 나옴 | `frontend`를 빌드하지 않은 상태입니다. `--dev`로 실행하거나 `cd frontend; npm run build` 실행 |
| 개발 모드에서 API 오류 | 백엔드가 `8765` 포트에서 실행 중인지 확인 |

## 한계

4B 모델은 복잡한 경제 분석에 한계가 있습니다. 동기화되지 않은 최신 수치나 인과관계는 틀릴 수 있으므로 중요 수치는 ECOS 원자료를 확인하세요.

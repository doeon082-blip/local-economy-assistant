# 로컬 경제 연구실

[![CI](https://github.com/doeon082-blip/local-economy-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/doeon082-blip/local-economy-assistant/actions/workflows/ci.yml)

Ollama의 Qwen3.5 4B 모델과 한국은행 ECOS 경제통계를 사용하는 한국어 경제 대화 앱입니다. FastAPI 백엔드, React 화면, SQLite 원자료 저장소, 로컬 Chroma 벡터 DB로 구성됩니다. 질문은 기본적으로 내 PC의 Ollama에서만 처리됩니다.

## 목차

- [주요 기능](#주요-기능)
- [동작 방식](#동작-방식)
- [기술 스택](#기술-스택)
- [개인정보 및 연결 범위](#개인정보-및-연결-범위)
- [로컬에서 시작하기](#로컬에서-시작하기)
- [ECOS 설정](#ecos-설정)
- [환경 변수](#환경-변수)
- [API 명세](#api-명세)
- [Docker / 서버 배포](#docker--서버-배포)
- [무료 호스팅 배포](#무료-호스팅-배포)
- [테스트와 CI](#테스트와-ci)
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
- ECOS 동기화는 승인이 필요한 작업입니다. 서버가 발급한 승인 토큰(또는 `SYNC_ADMIN_KEY`)을 `X-Local-Approval` 헤더로 보내야 하며, 없거나 틀리면 거절합니다.

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
  uv sync
  uv run python src/main.py
  ```

  *(또는 `.\start.cmd` 실행)*

- **통합 개발 모드** — FastAPI `--reload`와 Vite 개발 서버(`http://127.0.0.1:5173`)를 한 번에 띄우고 API를 자동 프록시합니다.

  ```powershell
  uv run python src/main.py --dev
  ```

  *(또는 `.\dev.cmd` 실행)*

### 3. 처음 사용하기

1. 화면 왼쪽 아래 **ECOS 데이터** 카드를 눌러 가져오기 창을 엽니다.
2. **주요 4대 지표 한 번에 받기**를 누르면 금리·환율·물가·GDP가 저장됩니다.
3. 저장된 건수는 카드에 `DB n건 · 벡터 n건`으로 표시됩니다. 이제 "기준금리의 경제적 영향을 설명해줘" 같은 질문을 해 보세요.

## ECOS 설정

[한국은행 ECOS Open API](https://ecos.bok.or.kr/api/)에서 키를 발급받고 프로젝트 루트의 `.env`에 입력할 수 있습니다. `.env`는 Git에서 제외됩니다.

```dotenv
ECOS_API_KEY=발급받은_개인_API_키
```

| 구분 | 키 없음 (샘플 키) | 개인 API 키 |
| --- | --- | --- |
| 가져오기 한도 | 요청당 최대 10건 | 지표당 최대 50,000건 |
| 페이지네이션 | 없음 | 1,000건 단위 자동 |
| 4대 지표 병렬 수집 | 가능(10건 한도) | 가능 |

이미 저장·임베딩된 동일 관측치는 재임베딩을 건너뜁니다.

### 주기별 기간 형식

| 주기 | 기간 형식 | 예시 |
| --- | --- | --- |
| `D` 일간 | `YYYYMMDD` | `20260102` |
| `M` 월간 | `YYYYMM` | `202601` |
| `Q` 분기 | `YYYYQn` | `2026Q1` |
| `A` 연간 | `YYYY` | `2026` |

## 환경 변수

`.env.example`을 `.env`로 복사해 필요한 값만 바꿉니다. 모든 값은 선택 사항입니다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `ECOS_API_KEY` | (비어 있음) | ECOS 개인 API 키. 비면 샘플 키로 최대 10건 |
| `OLLAMA_MODEL` | `qwen3.5:4b` | 채팅 모델 이름 |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama 서버 주소. 원격 주소는 `ALLOW_REMOTE_OLLAMA=true`일 때만 허용 |
| `OLLAMA_NUM_CTX` | `4096` | 컨텍스트 길이 |
| `OLLAMA_NUM_PREDICT` | `768` | 답변 최대 토큰 수 |
| `OLLAMA_KEEP_ALIVE` | `30m` | 모델을 메모리에 유지하는 시간 |
| `EMBED_BATCH_SIZE` | `128` | 임베딩 배치 크기 |
| `EMBED_WORKERS` | `3` | 임베딩 병렬 워커 수 |
| `ALLOW_REMOTE_ACCESS` | `false` | `true`면 루프백 외 접속 허용 (서버 배포용) |
| `ALLOW_REMOTE_OLLAMA` | `false` | `true`면 원격 Ollama 사용 허용 |
| `HOST` | `127.0.0.1` | 바인딩 주소. 컨테이너에서는 `0.0.0.0` |
| `PORT` | `8765` | 서버 포트 |
| `SYNC_ADMIN_KEY` | (비어 있음) | 설정하면 ECOS 동기화에 이 키가 필요하고 화면에 승인 토큰을 노출하지 않음 |
| `CORS_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | 별도 프론트엔드 도메인을 쉼표로 구분해 지정 |
| `VITE_API_BASE` | (비어 있음) | 프론트엔드 **빌드** 시 백엔드 주소. 정적 호스팅에 올릴 때 사용 |

## API 명세

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `GET` | `/healthz` | 헬스체크 (원격 접근 제한에서 제외) |
| `GET` | `/api/status` | Ollama·모델 상태, DB/벡터 건수, 동기화 요구 여부 |
| `GET` | `/api/data/observations` | 저장된 관측치 조회. `stat_code`, `limit`(1~500) |
| `POST` | `/api/chat` | 한 번에 응답하는 채팅 |
| `POST` | `/api/chat/stream` | NDJSON 스트리밍 채팅 |
| `POST` | `/api/data/sync` | 단일 지표 동기화 (승인 필요) |
| `POST` | `/api/data/sync/batch` | 최대 12개 지표 병렬 동기화 (승인 필요) |

### 채팅 요청

```json
{
  "question": "기준금리의 경제적 영향을 설명해줘",
  "history": [{ "role": "user", "content": "이전 질문" }]
}
```

`question`은 1~8,000자, `history`는 최대 12개입니다.

### 스트리밍 응답 형식

한 줄에 JSON 하나씩 순서대로 전달됩니다.

```text
{"type":"meta","model":"qwen3.5:4b","citations":[{"label":"...","url":"..."}]}
{"type":"delta","content":"기준금리는"}
{"type":"delta","content":" ..."}
{"type":"done"}
```

생성 중 오류가 나면 `{"type":"error","detail":"..."}`가 전달됩니다.

### 동기화 요청

`X-Local-Approval` 헤더에 승인 토큰(`/api/status`의 `approval_token`) 또는 `SYNC_ADMIN_KEY` 값을 넣어야 합니다.

```json
{
  "stat_code": "722Y001",
  "item_code": "0101000",
  "cycle": "D",
  "start": "20200101",
  "end": "20261231",
  "limit": 5000
}
```

오류 코드: `403` 승인 실패·정책 거절, `400` ECOS 요청 오류, `422` 입력 검증 실패, `503` Ollama 또는 내부 서비스 오류.

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
   - SQLite와 Chroma 데이터는 `vector_data` 볼륨(`/app/.local_data`)에, Ollama 모델은 `ollama_data` 볼륨에 보관되어 컨테이너를 다시 만들어도 유지됩니다.

3. 단일 컨테이너 PaaS(Cloud Run, Railway, Render, Hugging Face Spaces 등)에서는 `Dockerfile`을 배포하고 `ALLOW_REMOTE_ACCESS=true`, `ALLOW_REMOTE_OLLAMA=true`, `OLLAMA_URL`, `PORT`를 플랫폼에 맞게 지정하세요.

## 무료 호스팅 배포

| 대상 | 설정 파일 | 비고 |
| --- | --- | --- |
| 백엔드 (Render) | `render.yaml` | Docker 런타임, 무료 플랜, `/healthz` 헬스체크, `SYNC_ADMIN_KEY` 자동 생성 |
| 프론트엔드 (GitHub Pages) | `.github/workflows/pages.yml` | 푸시 시 자동 빌드·배포, `VITE_API_BASE` 변수 사용 |
| 프론트엔드 (Netlify) | `netlify.toml` | `frontend`에서 `npm run build`, `dist` 게시, Node 24 |

배포 순서:

1. Render에서 이 저장소로 Blueprint를 만들고 `OLLAMA_URL`(접근 가능한 Ollama 서버)과 필요하면 `ECOS_API_KEY`를 입력합니다.
2. Netlify 빌드 환경 변수 `VITE_API_BASE`에 Render 서비스 주소를 지정합니다.
3. Render 쪽 `CORS_ORIGINS`에 Netlify 도메인을 추가합니다.

### GitHub Pages (프론트엔드 자동 배포)

`.github/workflows/pages.yml`이 `main`에 `frontend/**` 변경이 푸시될 때마다 화면을 빌드해 GitHub Pages로 배포합니다. 주소는 `https://<계정>.github.io/local-economy-assistant/`입니다.

처음 한 번만 저장소 설정이 필요합니다.

1. **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 바꿉니다. (무료 계정은 공개 저장소여야 합니다.)
2. **Settings → Secrets and variables → Actions → Variables**에 `VITE_API_BASE`를 추가하고 값으로 HTTPS 백엔드 주소(예: Render 서비스 주소)를 넣습니다.
3. 백엔드의 `CORS_ORIGINS`에 `https://<계정>.github.io`를 추가합니다.
4. **Actions → Deploy frontend to GitHub Pages → Run workflow**로 수동 실행하거나 `frontend/`를 수정해 푸시합니다.

> Pages는 HTTPS로 제공되므로 백엔드도 HTTPS여야 브라우저가 호출할 수 있습니다. `VITE_API_BASE`가 없으면 화면은 열리지만 서버에 연결되지 않습니다.

> 무료 플랜 서버에는 Ollama 모델을 올릴 수 없으므로, 외부에서 접근 가능한 Ollama 서버가 따로 필요합니다. 무료 인스턴스는 유휴 시 잠들고 디스크가 영구 보존되지 않을 수 있어, 재시작 후에는 ECOS 데이터를 다시 가져와야 할 수 있습니다.

## 테스트와 CI

```powershell
uv run python -m unittest discover -s tests -v
```

테스트 25개가 다음을 검증합니다. 외부 서비스(ECOS, Ollama)는 모의 객체로 대체되어 네트워크 없이 실행됩니다.

| 파일 | 대상 |
| --- | --- |
| `tests/test_api.py` | 상태·채팅·스트리밍·동기화 API, 루프백 제한 |
| `tests/test_config_and_policy.py` | 환경 설정, 보안 정책(fail-closed) |
| `tests/test_ecos_and_vector_store.py` | ECOS 페이지네이션·키 가림, SQLite·증분 임베딩 |
| `tests/test_assistant_and_db_queries.py` | 프롬프트 구성, SQLite 조회 |
| `tests/test_ollama_client.py` | Ollama 일반·스트리밍 응답과 오류 처리 |

GitHub Actions(`.github/workflows/ci.yml`)가 `main` 푸시와 PR마다 백엔드 테스트 → 프론트엔드 빌드 → Docker 이미지 빌드 검증을 순서대로 실행합니다.

## 프로젝트 구조

```text
├── Dockerfile                 # Node 24 + Python 3.13 (uv) 멀티스테이지 빌드
├── docker-compose.yml         # 앱 + Ollama + 모델 초기화 구성
├── render.yaml                # Render 백엔드 배포 설정
├── netlify.toml               # Netlify 프론트엔드 배포 설정
├── .env.example               # 로컬/배포 환경 변수 템플릿
├── .github/workflows/ci.yml   # 백엔드 테스트, 프론트 빌드, Docker 빌드 검증
├── start.cmd / dev.cmd        # 통합 실행 / 통합 개발 모드 실행
├── src/
│   ├── main.py                # 통합 실행기 (--dev 지원)
│   └── economy_assistant/
│       ├── config.py              # 로컬/원격 실행, 모델 및 API 키 설정
│       ├── api.py                 # FastAPI 서버 및 정적 프론트엔드 서빙
│       ├── security_policy.py     # API 작업 허용 및 동기화 승인 검사
│       ├── data/
│       │   ├── ecos_client.py     # ECOS 데이터 동기화 및 검증
│       │   ├── sqlite_store.py    # ECOS 원자료 및 동기화 이력 즉시 SQLite 저장
│       │   └── vector_store.py    # Chroma DB 및 Ollama 임베딩
│       └── services/
│           ├── ollama_client.py   # Ollama 연결, 스트리밍, 모델 상태 확인
│           └── economic_assistant.py  # 하이브리드 검색과 프롬프트 구성
├── frontend/                  # React + Vite UI
└── tests/                     # 단위 및 API 통합 테스트
```

## 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 화면 상단에 연결 안 됨 표시 | Ollama가 실행 중인지, `OLLAMA_URL`이 맞는지 확인 |
| 모델 또는 임베딩 모델 없음 | `ollama pull qwen3.5:4b`, `ollama pull nomic-embed-text` 실행 |
| 가져오기가 10건에서 멈춤 | `.env`에 개인 `ECOS_API_KEY`를 설정했는지 확인 |
| 동기화가 403으로 거절됨 | `SYNC_ADMIN_KEY`를 설정했다면 화면의 관리자 키 칸에 같은 값 입력 |
| 다른 기기에서 접속이 403 | 의도된 동작입니다. 서버 배포라면 `ALLOW_REMOTE_ACCESS=true`와 `HOST=0.0.0.0` 설정 |
| 화면이 안 보이고 JSON만 나옴 | `frontend`를 빌드하지 않은 상태입니다. `--dev`로 실행하거나 `npm run build` 실행 |
| 개발 모드에서 API 오류 | 백엔드가 `8765` 포트에서 실행 중인지 확인 |

## 한계

4B 모델은 복잡한 경제 분석에 한계가 있습니다. 동기화되지 않은 최신 수치나 인과관계는 틀릴 수 있으므로 중요 수치는 ECOS 원자료를 확인하세요.

# 수업 도우미 (class_assistant)

수업 자료(PDF·PPT·노트·자막)를 올리면 **Q&A, 퀴즈·복습, 요약·용어집, 코드·에러 도우미, 진도**를 제공하는 로컬 우선 AI 앱의 초안입니다. 경제 앱(`economy_assistant`)의 구조를 바탕으로 같은 저장소의 별도 패키지로 만들었습니다. [← README로](../README.md)

## 실행

```powershell
uv sync --project backend --extra ui          # 최초 1회 (Streamlit 포함)
scripts\class-start.cmd                       # API(8800) + 임시 화면(8501)을 각각 새 창에서 실행
```

수동으로 실행하려면:

```powershell
uv run --project backend class-assistant                                  # API  http://127.0.0.1:8800
uv run --project backend --extra ui streamlit run frontend_streamlit/app.py  # 화면 http://localhost:8501
```

Ollama와 모델(`qwen3.5:4b`, `nomic-embed-text`)이 필요합니다. 설정은 `.env`의 `CLASS_*`, `MAX_UPLOAD_MB`, `EMBED_MODEL` 값으로 바꿉니다(`.env.example` 참고).

## 구조

```text
backend/src/class_assistant/
├── config.py                  # 경로·환경 변수 (.env는 저장소 루트)
├── llm/
│   ├── base.py                # LLMProvider / Embedder 인터페이스 (기능은 이것에만 의존)
│   ├── ollama.py              # Ollama 구현 (chat, stream, embed, health)
│   └── __init__.py            # register_provider / build_provider
├── ingest/
│   ├── extractors.py          # 형식별 텍스트 추출 + register_extractor
│   └── chunker.py             # 조각 분할 (출처 위치 locator 유지)
├── store/
│   ├── database.py            # SQLite: 문서·조각·문제·풀이 기록·요약 캐시
│   └── vectors.py             # ChunkIndex 인터페이스 + Chroma 구현
├── services.py                # Services 컨테이너, Library(업로드→추출→저장→색인)
├── features/
│   ├── base.py                # Feature / ChatFeature / register_feature
│   ├── qa.py                  # 자료 Q&A (번호 인용)
│   ├── coding_help.py         # 힌트→단계→정답 3단계 도우미
│   ├── quiz.py                # 문제 생성·채점·오답 우선 복습
│   ├── summary.py             # 요약·용어집·복습 질문 (긴 문서는 map-reduce)
│   └── progress.py            # 주제별 정답률, 약점 추천
└── api.py                     # FastAPI (create_app 팩토리)
frontend_streamlit/app.py      # 임시 화면 — HTTP API만 호출
```

데이터는 `.local_data/class_assistant/`(SQLite, Chroma)에 저장되며 Git에서 제외됩니다.

## API

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `GET` | `/api/status`, `/api/features` | 모델·건수·지원 형식, 등록된 기능 목록 |
| `POST` | `/api/documents` | 파일 업로드(multipart `file`). 새 파일 201, 동일 파일 200 |
| `GET` / `DELETE` | `/api/documents`, `/api/documents/{id}` | 목록 / 삭제(DB·벡터 함께) |
| `POST` | `/api/documents/{id}/summary?refresh=` | 요약·용어집·복습 질문 (캐시됨) |
| `POST` | `/api/chat/stream` | NDJSON 스트리밍. `mode`: `qa` 또는 `code` |
| `POST` | `/api/quiz/generate` | `{doc_ids?, count(1~10), kind: mcq\|short}` |
| `GET` | `/api/quiz/review?limit=` | 틀린 문제 → 안 푼 문제 → 적게 푼 문제 순 |
| `POST` | `/api/quiz/{id}/answer` | `{answer}` — 객관식은 **보기 번호(0부터)** 를 보냅니다 |
| `GET` | `/api/progress` | 주제별 정답률, `weak_topics` |

스트리밍 이벤트: `meta`(출처 등) → `delta`* → `done` (오류 시 `error`). 오류 코드: 400 빈 파일, 413 용량 초과, 415 지원하지 않는 형식, 422 읽을 수 없는 파일·잘못된 요청, 503 모델 오류.

## 확장하는 법

| 하고 싶은 것 | 방법 |
| --- | --- |
| **새 파일 형식** (예: `.docx`, `.csv`) | `register_extractor(".docx", fn)` — `fn(bytes) -> list[Segment]` |
| **새 기능** (예: 플래시카드 내보내기, 과제 도우미) | `features/`에 `@register_feature` 클래스 추가 후 `features/__init__.py`에 import. `ChatFeature`를 상속하면 `mode`로 채팅에 자동 노출 |
| **다른 모델** (클라우드 API 등) | `register_provider("name", factory)` 후 `LLM_PROVIDER=name`. 기능 코드는 수정하지 않음 |
| **다른 벡터 DB** | `ChunkIndex`(4개 메서드)를 구현해 `Services(index=...)`에 주입 |
| **React 화면** | `frontend_streamlit/`을 대체. API는 그대로 |

기능 모듈은 `Services`(db, index, llm, library)만 받으므로, 테스트에서는 가짜 LLM·인덱스를 주입합니다(`backend/tests/test_class_assistant.py`).

## 설계 판단

- **퀴즈는 근거 구절을 함께 저장합니다.** 4B 모델이 만든 문제는 틀릴 수 있어서, 채점 후 근거 자료를 보여 줘 학습자가 직접 검증하게 합니다.
- **문제는 자료 전체에 골고루, 겹치지 않게 출제합니다.** 문제가 가장 적게 나온 조각부터 무작위로 고르고, 요청한 문제 수가 조각 수보다 많으면 한 조각에서 **다른 관점**(정의·비교·예시·원인결과·옳지 않은 것·적용)으로 여러 번 출제합니다. 이미 낸 문제는 프롬프트에 "겹치지 말 것"으로 알려 주고, 결과도 문자 2-gram 유사도(0.7 이상이면 같은 문제)로 검사해 저장하지 않습니다. 더 낼 문제가 없으면 억지로 채우지 않고 `notice`로 알립니다.
- **프롬프트 지시문이 문제에 새어 나오면 버립니다.** 작은 모델이 "(단, … 사용하지 말라)" 같은 우리 지시를 문제 본문에 베끼는 일이 실제로 있었습니다.
- **단답형 채점은 정확 일치 → LLM 판정** 순이며, 모델이 응답하지 않으면 오답 처리합니다(점수를 후하게 주지 않음).
- **업로드는 인덱싱이 실패하면 DB에서도 되돌립니다.** SQLite와 벡터 DB가 어긋난 문서가 남지 않게 합니다.
- **자료와 로그 안의 지시문은 따르지 않도록** `<untrusted_data>`로 감싸고, 채팅 이력은 `user`/`assistant` 역할만 받습니다.
- **원격 공개 시 `CLASS_API_KEY`가 없으면 모든 API를 거절**합니다(fail closed). 기본은 루프백 전용입니다.

## 속도

모델이 메모리에 올라가 있으면 빠릅니다(실측: 퀴즈 1문제 약 1.3초, Q&A 첫 글자 약 1.5초). 느려지는 주된 원인은 **모델 재로딩**이라 다음을 적용했습니다.

- 모든 호출에 `keep_alive`(기본 30분, `OLLAMA_KEEP_ALIVE`)를 보내 쉬었다 와도 모델이 내려가지 않게 합니다. Ollama 기본값은 5분입니다.
- API 서버 시작 시 백그라운드로 채팅·임베딩 모델을 미리 올립니다(warm-up).
- 퀴즈는 조각별로 병렬 생성합니다. 다만 Ollama가 한 번에 한 요청만 처리하면 효과가 작습니다(실측 6문제 약 11~13초). `OLLAMA_NUM_PARALLEL=4`로 Ollama를 실행하면 병렬 이득을 얻지만 메모리를 더 씁니다.
- 더 빠르게 하려면 클라우드 모델 provider를 등록하는 것이 가장 효과적입니다(아래 권장안 참고).

## 알려진 한계 (초안)

- 스캔 이미지 PDF(OCR), 음성 파일(STT)은 지원하지 않습니다. 녹취는 텍스트·자막으로 올립니다.
- 단일 사용자 가정입니다. 사용자별 계정·진도 분리는 없습니다.
- 요청 제한(rate limit)은 아직 없습니다. 공개 배포 전에 `economy_assistant`의 `RateLimiter`를 가져다 적용하세요.
- 경제 앱과 일부 코드(설정 읽기, 보안 헤더)가 중복되어 있어, 이후 공통 패키지로 합치는 것이 좋습니다.

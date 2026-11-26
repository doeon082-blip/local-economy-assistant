# 설정

ECOS 키와 환경 변수 설정 방법입니다. [← README로](../README.md)

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


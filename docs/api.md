# API 명세

백엔드 HTTP API입니다. [← README로](../README.md)


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

`question`은 1~8,000자, `history`는 최대 12개이며 각 항목의 `role`은 `user` 또는 `assistant`만 허용하고 `content`는 8,000자까지입니다(그 외는 422).

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

`X-Local-Approval` 헤더에 값을 넣어야 합니다.

- 로컬 모드: `/api/status`의 `approval_token`
- 서버 공개 모드(`ALLOW_REMOTE_ACCESS=true`): `SYNC_ADMIN_KEY` 값. 이 키가 설정되지 않았다면 동기화가 비활성화되고(`sync_enabled: false`) 승인 토큰도 내려가지 않습니다.

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

오류 코드: `403` 승인 실패·정책 거절, `429` 요청 제한 초과 또는 인증 실패 잠금(`Retry-After: 60`), `400` ECOS 요청 오류, `422` 입력 검증 실패, `503` Ollama 또는 내부 서비스 오류.


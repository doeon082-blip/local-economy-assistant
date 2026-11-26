# 배포

Docker, Render, Netlify, GitHub Pages 배포 방법입니다. [← README로](../README.md)

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


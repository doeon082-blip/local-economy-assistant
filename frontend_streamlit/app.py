"""TEMPORARY prototype UI (Streamlit). It only calls the HTTP API, so it can be replaced by React
without touching the backend.

Run:  uv run --project backend --extra ui streamlit run frontend_streamlit/app.py
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator

import httpx
import streamlit as st

API_URL = os.environ.get("CLASS_API_URL", "http://127.0.0.1:8800").rstrip("/")
HEADERS = {"X-API-Key": os.environ["CLASS_API_KEY"]} if os.environ.get("CLASS_API_KEY") else {}

st.set_page_config(page_title="수업 도우미", page_icon="📚", layout="wide")


class ApiError(RuntimeError):
    pass


def call(method: str, path: str, **kwargs):
    try:
        response = httpx.request(method, API_URL + path, headers=HEADERS, timeout=kwargs.pop("timeout", 300), **kwargs)
    except httpx.RequestError as exc:
        raise ApiError(f"API 서버({API_URL})에 연결할 수 없습니다. 먼저 백엔드를 실행하세요.") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ApiError(f"{detail} (HTTP {response.status_code})")
    return response.json()


def stream_chat(payload: dict, meta_sink: dict) -> Iterator[str]:
    """Yield answer tokens; stash the leading meta event (citations, ...) into ``meta_sink``."""
    try:
        with httpx.stream("POST", API_URL + "/api/chat/stream", json=payload, headers=HEADERS, timeout=None) as response:
            if response.status_code != 200:
                response.read()
                raise ApiError(response.json().get("detail", "요청 실패"))
            for line in response.iter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if event["type"] == "meta":
                    meta_sink.update(event)
                elif event["type"] == "delta":
                    yield event["content"]
                elif event["type"] == "error":
                    raise ApiError(event["detail"])
    except httpx.RequestError as exc:
        raise ApiError("API 서버에 연결할 수 없습니다.") from exc


def show_error(exc: Exception) -> None:
    st.error(str(exc))


# --- sidebar: status + library ----------------------------------------------------
try:
    status = call("GET", "/api/status", timeout=10)
    documents = call("GET", "/api/documents")["documents"]
except ApiError as exc:
    st.error(str(exc))
    st.stop()

with st.sidebar:
    st.title("📚 수업 도우미")
    llm = status["llm"]
    st.caption(("🟢" if llm.get("connected") and llm.get("model_available") and llm.get("embed_model_available") else "🔴")
               + f" {llm.get('model', '?')} · 문서 {status['counts']['documents']}개")
    if not llm.get("connected"):
        st.warning("모델 서버(Ollama)에 연결되지 않았습니다.")

    st.subheader("자료")
    uploads = st.file_uploader(
        "파일 올리기", accept_multiple_files=True, type=[e.lstrip(".") for e in status["extensions"]],
        help=f"최대 {status['max_upload_mb']}MB · 같은 파일은 한 번만 저장됩니다.",
    )
    if uploads and st.button("업로드·색인", type="primary"):
        for upload in uploads:
            with st.spinner(f"{upload.name} 처리 중…"):
                try:
                    result = call("POST", "/api/documents", files={"file": (upload.name, upload.getvalue())})
                    st.success(f"{upload.name}: " + ("저장됨" if result["created"] else "이미 있는 파일"))
                except ApiError as exc:
                    st.error(f"{upload.name}: {exc}")
        st.rerun()

    labels = {d["id"]: f"{d['filename']} ({d['chunk_count']}조각)" for d in documents}
    scope = st.multiselect("질문 범위 (비우면 전체)", list(labels), format_func=labels.get)
    with st.expander("자료 삭제"):
        for d in documents:
            if st.button(f"🗑 {d['filename']}", key=f"del{d['id']}"):
                call("DELETE", f"/api/documents/{d['id']}")
                st.rerun()

tab_chat, tab_quiz, tab_summary, tab_progress = st.tabs(["💬 질문하기", "📝 퀴즈·복습", "📄 요약", "📈 진도"])

# --- chat -------------------------------------------------------------------------
with tab_chat:
    mode = st.radio("모드", ["qa", "code"], horizontal=True, format_func={"qa": "자료 Q&A", "code": "코드·에러 도우미"}.get)
    level = 1
    if mode == "code":
        level = st.select_slider("도움 수준", [1, 2, 3], format_func={1: "1 힌트", 2: "2 단계 안내", 3: "3 정답 공개"}.get)
    history = st.session_state.setdefault("history", {"qa": [], "code": []})[mode]
    if st.button("대화 초기화"):
        history.clear()
        st.rerun()

    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            for c in message.get("citations", []):
                st.caption(f"[{c['n']}] {c['filename']} · {c['locator']} — {c['snippet']}…")

    if question := st.chat_input("질문을 입력하세요" if mode == "qa" else "에러 로그나 코드를 붙여 넣으세요"):
        with st.chat_message("user"):
            st.markdown(question)
        payload = {
            "question": question, "mode": mode, "level": level, "doc_ids": scope or None,
            "history": [{"role": m["role"], "content": m["content"]} for m in history[-12:]],
        }
        meta: dict = {}
        with st.chat_message("assistant"):
            try:
                answer = st.write_stream(stream_chat(payload, meta))
                citations = meta.get("citations", [])
                for c in citations:
                    st.caption(f"[{c['n']}] {c['filename']} · {c['locator']} — {c['snippet']}…")
                history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer, "citations": citations}]
            except ApiError as exc:
                show_error(exc)

# --- quiz -------------------------------------------------------------------------
with tab_quiz:
    col_a, col_b, col_c, col_d = st.columns([1, 1, 1, 1])
    count = col_a.number_input("문제 수", 1, 10, 3)
    kind = col_b.selectbox("유형", ["mcq", "short"], format_func={"mcq": "객관식", "short": "단답형"}.get)
    if col_c.button("새 문제 만들기", type="primary"):
        with st.spinner("문제를 만드는 중… (모델에 따라 수십 초)"):
            try:
                result = call("POST", "/api/quiz/generate", json={"doc_ids": scope or None, "count": int(count), "kind": kind})
                st.session_state["quiz"] = {"items": result["items"], "results": {}}
                if result["skipped"]:
                    st.info(f"모델이 형식에 맞지 않게 답해 {result['skipped']}개는 건너뛰었습니다.")
            except ApiError as exc:
                show_error(exc)
    if col_d.button("틀린 문제 복습"):
        try:
            st.session_state["quiz"] = {"items": call("GET", "/api/quiz/review", params={"limit": int(count)})["items"], "results": {}}
        except ApiError as exc:
            show_error(exc)

    quiz = st.session_state.get("quiz", {"items": [], "results": {}})
    for number, item in enumerate(quiz["items"], start=1):
        with st.container(border=True):
            st.markdown(f"**Q{number}. {item['question']}**  \n:gray[주제: {item['topic']}]")
            if item["kind"] == "mcq":
                choice = st.radio("보기", range(len(item["choices"])), format_func=lambda i, it=item: it["choices"][i],
                                  key=f"a{item['id']}", index=None, label_visibility="collapsed")
                answer = None if choice is None else str(choice)
            else:
                answer = st.text_input("답", key=f"a{item['id']}", label_visibility="collapsed")
            if st.button("채점", key=f"g{item['id']}", disabled=not answer):
                try:
                    quiz["results"][item["id"]] = call("POST", f"/api/quiz/{item['id']}/answer", json={"answer": answer})
                except ApiError as exc:
                    show_error(exc)
            result = quiz["results"].get(item["id"])
            if result:
                (st.success if result["correct"] else st.error)("정답입니다 ✅" if result["correct"] else f"오답 ❌ — 정답: {result['correct_answer']}")
                st.caption(result["explanation"])
                with st.expander("근거 자료 (모델이 만든 문제라 틀릴 수 있어요. 직접 확인하세요)"):
                    st.write(result["source_text"])

# --- summary ----------------------------------------------------------------------
with tab_summary:
    if not documents:
        st.info("왼쪽에서 자료를 먼저 올려 주세요.")
    else:
        pick = st.selectbox("문서", list(labels), format_func=labels.get)
        refresh = st.checkbox("다시 생성")
        if st.button("요약하기", type="primary"):
            with st.spinner("요약 중…"):
                try:
                    st.session_state["summary"] = call("POST", f"/api/documents/{pick}/summary", params={"refresh": refresh})
                except ApiError as exc:
                    show_error(exc)
        if (data := st.session_state.get("summary")):
            st.markdown("#### 요약")
            st.write(data["summary"])
            if data["glossary"]:
                st.markdown("#### 핵심 용어")
                st.table({"용어": [g["term"] for g in data["glossary"]], "설명": [g["definition"] for g in data["glossary"]]})
            if data["questions"]:
                st.markdown("#### 복습 질문")
                for q in data["questions"]:
                    st.markdown(f"- {q}")

# --- progress ---------------------------------------------------------------------
with tab_progress:
    try:
        report = call("GET", "/api/progress")
        c1, c2 = st.columns(2)
        c1.metric("풀이 횟수", report["attempts"])
        c2.metric("전체 정답률", "-" if report["accuracy"] is None else f"{report['accuracy']:.0%}")
        if report["weak_topics"]:
            st.warning("복습 추천 주제: " + ", ".join(report["weak_topics"]))
        if report["topics"]:
            st.dataframe(report["topics"], hide_index=True, width="stretch")
        else:
            st.info("퀴즈를 풀면 주제별 정답률이 여기에 표시됩니다.")
    except ApiError as exc:
        show_error(exc)

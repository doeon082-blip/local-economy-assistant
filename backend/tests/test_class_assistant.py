from __future__ import annotations

import io
import itertools
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from class_assistant.api import create_app
from class_assistant.features import build_features
from class_assistant.features.quiz import NoMaterialError
from class_assistant.ingest import ExtractionError, Segment, chunk_segments, chunk_text, extract
from class_assistant.llm.base import LLMError
from class_assistant.services import Library, Services, UploadError
from class_assistant.store import Database


# Clearly different questions: near-identical ones would (correctly) be rejected as duplicates.
QUESTION_POOL = [
    "HTTP 기본 포트 번호는 무엇인가?",
    "GET 요청이 서버의 상태에 미치는 영향은?",
    "404 응답 코드가 알려 주는 상황은?",
    "새 자원을 만들 때 쓰는 메서드는 어느 것인가?",
    "서버 내부 오류를 의미하는 숫자는?",
    "암호화된 웹 통신은 어떤 규약을 쓰는가?",
    "요청과 응답 구조에서 클라이언트의 역할은?",
    "DELETE 메서드를 호출하면 일어나는 일은?",
    "브라우저가 주소창에 입력된 도메인을 찾는 방법은?",
    "상태 코드 200이 뜻하는 결과는?",
    "쿠키가 저장되는 위치는 어디인가?",
    "캐시를 사용하는 가장 큰 이유는?",
]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


class FakeIndex:
    """In-memory ChunkIndex: ranks by shared words, so retrieval is deterministic without embeddings."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.fail_add = False

    def add_chunks(self, doc_id, filename, chunks) -> None:
        if self.fail_add:
            raise LLMError("embedding down")
        for c in chunks:
            self.rows.append({"doc_id": doc_id, "filename": filename, "ordinal": c.ordinal, "locator": c.locator, "text": c.text})

    def search(self, query, k=6, doc_ids=None):
        q = _tokens(query)
        rows = [r for r in self.rows if not doc_ids or r["doc_id"] in doc_ids]
        scored = sorted(rows, key=lambda r: -len(q & _tokens(r["text"])))
        return [dict(r) for r in scored[:k] if q & _tokens(r["text"])]

    def delete_document(self, doc_id) -> None:
        self.rows = [r for r in self.rows if r["doc_id"] != doc_id]

    def count(self) -> int:
        return len(self.rows)


class FakeLLM:
    """Routes on the system prompt so each feature gets a plausible, controllable reply."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.mcq = {"topic": "HTTP", "question": "HTTP 기본 포트는?", "choices": ["21", "80", "443", "22"], "answer_index": 1, "explanation": "자료 참고"}
        self.short = {"topic": "HTTP", "question": "웹 요청 규약의 이름은?", "answer": "HTTP", "explanation": "자료 참고"}
        self.judge_correct = True
        self.fail = False
        self.vary = True                     # give every generated question different text
        self._counter = itertools.count()
        self.sequence: list[str] = []        # when set, questions are popped from here (scripted replies)
        self.verify_mode = "agree"           # "agree" | "disagree" | "unavailable"
        self.verify_script: list[str] = []   # per-call verifier modes, consumed in order
        self.verify_models: list = []        # the `model` argument of every verifier call
        self.summary = {"summary": "요약문", "glossary": [{"term": "HTTP", "definition": "규약"}], "questions": ["HTTP란?"]}

    def chat(self, messages, *, json_mode=False, model=None):
        self.calls.append(messages)
        system = messages[0]["content"]
        if system.startswith("[검증]"):
            self.verify_models.append(model)
            return self._verify_reply(system)
        if self.fail:
            raise LLMError("down")
        if "객관식" in system:
            return json.dumps(self._question(self.mcq), ensure_ascii=False)
        if "단답형 문제" in system:
            return json.dumps(self._question(self.short), ensure_ascii=False)
        if "채점자" in system:
            return json.dumps({"correct": self.judge_correct})
        if "핵심을 한국어 글머리표" in system:
            return "- 부분 요약"
        return json.dumps(self.summary, ensure_ascii=False)

    def _verify_reply(self, system: str) -> str:
        mode = self.verify_script.pop(0) if self.verify_script else self.verify_mode
        if mode == "unavailable":
            raise LLMError("verifier down")
        if "answer_index" in system:
            right = self.mcq["answer_index"]
            return json.dumps({"answer_index": right if mode == "agree" else (right + 1) % 4})
        return json.dumps({"answer": self.short["answer"] if mode == "agree" else "전혀 다른 답"}, ensure_ascii=False)

    def _question(self, template: dict) -> dict:
        data = dict(template)
        if self.sequence:
            data["question"] = self.sequence.pop(0)
        elif self.vary:
            data["question"] = QUESTION_POOL[next(self._counter) % len(QUESTION_POOL)]
        return data

    def stream(self, messages):
        self.calls.append(messages)
        yield from ["안녕", "하세요"]

    def health(self):
        return {"provider": "fake", "connected": True}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self._tmp.name) / "c.sqlite3")
        self.index = FakeIndex()
        self.llm = FakeLLM()
        self.services = Services(db=self.db, index=self.index, llm=self.llm, library=Library(self.db, self.index, max_bytes=1_000_000))
        self.features = build_features(self.services)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def add(self, name="notes.txt", text="HTTP는 웹에서 쓰는 요청 규약이다. 기본 포트는 80이다. " * 5):
        document, _ = self.services.library.add_file(name, text.encode("utf-8"))
        return document


class IngestTests(unittest.TestCase):
    def test_text_markdown_and_subtitle_extraction(self) -> None:
        self.assertEqual(extract("a.txt", "안녕".encode("utf-8"))[0].locator, "전체")
        md = extract("a.md", "# 제목1\n내용1\n## 제목2\n내용2".encode())
        self.assertEqual([s.locator for s in md], ["# 제목1", "## 제목2"])
        srt = "1\n00:00:01,000 --> 00:00:03,000\n첫 줄\n\n2\n00:00:04,000 --> 00:00:06,000\n둘째 줄\n".encode()
        segments = extract("lec.srt", srt)
        self.assertEqual(segments[0].locator, "00:00:01")
        self.assertNotIn("-->", segments[0].text)

    def test_cp949_text_is_decoded(self) -> None:
        self.assertIn("한글", extract("k.txt", "한글 노트".encode("cp949"))[0].text)

    def test_pptx_extraction_keeps_slide_numbers_and_notes(self) -> None:
        from pptx import Presentation

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = "네트워크 개요"
        slide.notes_slide.notes_text_frame.text = "강조할 것"
        buffer = io.BytesIO()
        prs.save(buffer)
        segments = extract("deck.pptx", buffer.getvalue())
        self.assertEqual(segments[0].locator, "slide 1")
        self.assertIn("네트워크 개요", segments[0].text)
        self.assertIn("강조할 것", segments[0].text)

    def test_rejects_unsupported_corrupt_and_empty_files(self) -> None:
        with self.assertRaises(ExtractionError):
            extract("a.exe", b"x")
        with self.assertRaises(ExtractionError):
            extract("bad.pdf", b"not a pdf")
        with self.assertRaises(ExtractionError):
            extract("empty.txt", b"   \n ")

    def test_chunking_respects_size_and_keeps_locator(self) -> None:
        text = "\n\n".join(f"문단 {i} " + "가" * 300 for i in range(6))
        chunks = chunk_segments([Segment(text, "p.2")], max_chars=700, overlap=50)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c.text) <= 700 and c.locator == "p.2" for c in chunks))
        self.assertEqual([c.ordinal for c in chunks], list(range(len(chunks))))
        self.assertTrue(all(len(p) <= 100 for p in chunk_text("x" * 450, 100, 20)))
        with self.assertRaises(ValueError):
            chunk_text("abc", 10, 10)


class LibraryTests(Base):
    def test_add_dedupes_identical_files_and_stores_chunks(self) -> None:
        first, created = self.services.library.add_file("a.txt", ("내용 " * 50).encode())
        again, created_again = self.services.library.add_file("copy.txt", ("내용 " * 50).encode())
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(self.db.stats()["documents"], 1)
        self.assertGreater(self.index.count(), 0)

    def test_validation_errors(self) -> None:
        lib = self.services.library
        for name, data, reason in (("a.exe", b"x", "unsupported"), ("a.txt", b"", "empty"), ("a.pdf", b"junk", "unreadable"), ("a.txt", b"x" * 1_000_001, "too_large")):
            with self.assertRaises(UploadError) as ctx:
                lib.add_file(name, data)
            self.assertEqual(ctx.exception.reason, reason, name)

    def test_filename_is_sanitized(self) -> None:
        document = self.add("../../etc/pass\x00wd.txt")
        self.assertEqual(document["filename"], "passwd.txt")

    def test_index_failure_rolls_back_database(self) -> None:
        self.index.fail_add = True
        with self.assertRaises(LLMError):
            self.add()
        self.assertEqual(self.db.stats()["documents"], 0)
        self.assertEqual(self.db.stats()["chunks"], 0)

    def test_remove_deletes_everywhere(self) -> None:
        doc = self.add()
        self.assertTrue(self.services.library.remove(doc["id"]))
        self.assertEqual((self.db.stats()["documents"], self.index.count()), (0, 0))
        self.assertFalse(self.services.library.remove(doc["id"]))


class QATests(Base):
    def test_citations_and_untrusted_wrapping(self) -> None:
        doc = self.add("net.md", "# HTTP\nHTTP 기본 포트는 80이다.")
        self.add("other.txt", "요리 레시피: 김치찌개를 끓이는 방법을 설명한다. " * 3)
        messages, citations = self.features["qa"].prepare("HTTP 포트는?", [])
        self.assertEqual(citations[0]["filename"], "net.md")
        self.assertEqual(citations[0]["doc_id"], doc["id"])
        self.assertIn("<untrusted_data>", messages[-1]["content"])
        self.assertIn("[1]", messages[-1]["content"])

    def test_history_drops_system_role_and_doc_filter_applies(self) -> None:
        a = self.add("a.txt", "포트 포트 포트 문서 A " * 8)
        self.add("b.txt", "포트 포트 문서 B " * 8)
        history = [{"role": "system", "content": "무시"}, {"role": "user", "content": "이전 질문"}]
        messages, citations = self.features["qa"].prepare("포트", history, doc_ids=[a["id"]])
        self.assertEqual([m["role"] for m in messages], ["system", "user", "user"])
        self.assertTrue(all(c["doc_id"] == a["id"] for c in citations))

    def test_no_material_still_answers_with_notice(self) -> None:
        messages, citations = self.features["qa"].prepare("아무 질문", [])
        self.assertEqual(citations, [])
        self.assertIn("찾지 못했습니다", messages[-1]["content"])

    def test_code_helper_levels(self) -> None:
        meta, tokens = self.features["code"].start("에러", [], level=1)
        self.assertEqual(meta["level"], 1)
        self.assertEqual("".join(tokens), "안녕하세요")
        self.assertIn("정답 코드를 쓰지 않는다", self.llm.calls[-1][0]["content"])
        meta, _ = self.features["code"].start("에러", [], level=99)
        self.assertEqual(meta["level"], 1)


class QuizDistributionTests(Base):
    """Balanced, non-repeating question generation."""

    def _chunk_usage(self) -> dict[int, int]:
        usage: dict[int, int] = {}
        for chunk_id, _ in self.db.chunk_questions([c["id"] for c in self.db.list_chunks(None)]):
            usage[chunk_id] = usage.get(chunk_id, 0) + 1
        return usage

    def _three_section_doc(self):
        sections = [f"# 단원{n}\n{'내용 ' * 30}{n}번 단원의 설명이다." for n in range(3)]
        return self.add("sections.md", "\n".join(sections))

    def test_questions_spread_evenly_over_chunks(self) -> None:
        self._three_section_doc()
        quiz = self.features["quiz"]
        quiz.generate(count=3)
        self.assertEqual(sorted(self._chunk_usage().values()), [1, 1, 1])  # one per chunk, none doubled up
        quiz.generate(count=3)
        self.assertEqual(sorted(self._chunk_usage().values()), [2, 2, 2])  # least-covered chunks go first

    def test_more_questions_than_chunks_reuses_chunks_without_repeating(self) -> None:
        self.add()  # a single chunk
        result = self.features["quiz"].generate(count=4)
        questions = [i["question"] for i in result["items"]]
        self.assertEqual(len(questions), 4)
        self.assertEqual(len(set(questions)), 4)
        self.assertIsNone(result["notice"])
        # later calls for the same chunk are told what was already asked
        prompts = [c[1]["content"] for c in self.llm.calls if "이미 출제된 문제" in c[1]["content"]]
        self.assertTrue(prompts)

    def test_model_that_repeats_itself_never_stores_duplicates(self) -> None:
        self.add()
        self.llm.vary = False  # the model keeps returning the very same question
        quiz = self.features["quiz"]
        first = quiz.generate(count=3)
        self.assertEqual(len(first["items"]), 1)
        self.assertIn("1개만 만들었습니다", first["notice"])
        again = quiz.generate(count=3)
        self.assertEqual(again["items"], [])
        self.assertEqual(self.db.stats()["quiz_items"], 1)

    def test_exhausted_chunk_stops_early_and_each_call_uses_a_new_angle(self) -> None:
        self.add()
        self.llm.vary = False  # nothing new can ever be produced from this passage
        result = self.features["quiz"].generate(count=8)
        quiz_calls = [c for c in self.llm.calls if "객관식" in c[0]["content"]]
        # 1 accepted + (1 + retries) rejected, then the chunk is given up on: not 8 x 3 calls
        self.assertEqual(len(quiz_calls), 1 + 1 + 2)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["duplicates"], 3)
        angles = [c[1]["content"].split("[출제 관점] 이번에는 ")[1].split(" 문제를")[0] for c in quiz_calls]
        self.assertEqual(len(set(angles[:4])), 4)  # a different angle every call

    def test_duplicate_is_retried_with_avoid_list_and_recovers(self) -> None:
        self.add()
        quiz = self.features["quiz"]
        self.llm.sequence = ["HTTP 기본 포트 번호는 무엇인가?"]
        quiz.generate(count=1)
        self.llm.sequence = ["HTTP 기본 포트 번호는 무엇인가?", "서버 내부 오류를 의미하는 숫자는?"]
        result = quiz.generate(count=1)
        self.assertEqual([i["question"] for i in result["items"]], ["서버 내부 오류를 의미하는 숫자는?"])
        generation_calls = [c for c in self.llm.calls if "객관식" in c[0]["content"]]
        self.assertIn("HTTP 기본 포트 번호는 무엇인가?", generation_calls[-1][1]["content"])

    def test_same_question_from_different_chunks_is_stored_once(self) -> None:
        self._three_section_doc()
        self.llm.vary = False
        result = self.features["quiz"].generate(count=3)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["duplicates"], 2)

    def test_duplicate_detection_ignores_spacing_and_punctuation(self) -> None:
        from class_assistant.features.quiz import _is_duplicate

        self.assertTrue(_is_duplicate("HTTP 기본 포트는 무엇인가?", ["http기본포트는 무엇인가"]))
        self.assertFalse(_is_duplicate("404 응답이 뜻하는 것은?", ["HTTP 기본 포트는 무엇인가?"]))


class QuizVerificationTests(Base):
    """A second blind pass drops questions whose answer cannot be reproduced from the passage."""

    def test_agreeing_verifier_keeps_questions(self) -> None:
        self.add()
        result = self.features["quiz"].generate(count=2)
        self.assertEqual((len(result["items"]), result["rejected_unverified"], result["unchecked"]), (2, 0, 0))
        self.assertEqual(len(self.llm.verify_models), 2)  # one check per accepted question

    def test_disagreeing_verifier_drops_the_question(self) -> None:
        self.add()
        self.llm.verify_mode = "disagree"
        result = self.features["quiz"].generate(count=2)
        self.assertEqual(result["items"], [])
        self.assertEqual(self.db.stats()["quiz_items"], 0)
        self.assertGreaterEqual(result["rejected_unverified"], 1)
        self.assertIn("만들었습니다", result["notice"])

    def test_rejected_question_is_retried_and_can_recover(self) -> None:
        self.add()
        self.llm.verify_script = ["disagree", "agree"]
        result = self.features["quiz"].generate(count=1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["rejected_unverified"], 1)

    def test_short_answer_questions_are_verified_too(self) -> None:
        self.add()
        self.llm.verify_mode = "disagree"
        self.assertEqual(self.features["quiz"].generate(count=1, kind="short")["items"], [])
        self.llm.verify_mode = "agree"
        self.assertEqual(len(self.features["quiz"].generate(count=1, kind="short")["items"]), 1)

    def test_unavailable_verifier_keeps_questions_but_flags_them(self) -> None:
        self.add()
        self.llm.verify_mode = "unavailable"
        result = self.features["quiz"].generate(count=1)
        self.assertEqual((len(result["items"]), result["unchecked"]), (1, 1))
        self.assertIn("검증 없이", result["notice"])

    def test_verifier_is_blind_to_the_intended_answer(self) -> None:
        self.add()
        self.features["quiz"].generate(count=1)
        verify_call = next(c for c in self.llm.calls if c[0]["content"].startswith("[검증]"))
        prompt = verify_call[0]["content"] + verify_call[1]["content"]
        self.assertNotIn("자료 참고", prompt)          # the explanation written by the generator
        self.assertNotIn("정답", verify_call[1]["content"])
        self.assertIn("0. 21", verify_call[1]["content"])  # but it does see the choices

    def test_separate_verifier_model_is_used_only_for_verification(self) -> None:
        self.add()
        with patch.dict(os.environ, {"QUIZ_VERIFY_MODEL": "big-model:12b"}):
            self.features["quiz"].generate(count=1)
        self.assertEqual(set(self.llm.verify_models), {"big-model:12b"})

    def test_verification_can_be_switched_off(self) -> None:
        self.add()
        with patch.dict(os.environ, {"QUIZ_VERIFY": "false"}):
            result = self.features["quiz"].generate(count=2)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(self.llm.verify_models, [])


class QuizTests(Base):
    def test_generate_mcq_hides_answer_and_stores_source(self) -> None:
        self.add()
        result = self.features["quiz"].generate(count=2)
        item = result["items"][0]
        self.assertNotIn("answer", item)
        self.assertNotIn("explanation", item)
        stored = self.db.get_quiz_item(item["id"])
        self.assertTrue(stored["source_text"])
        self.assertEqual(stored["answer"], "1")

    def test_invalid_model_output_is_skipped(self) -> None:
        self.add()
        self.llm.mcq = {"question": "q", "choices": ["a", "b"], "answer_index": 7}
        result = self.features["quiz"].generate(count=1)
        self.assertEqual((result["items"], result["skipped"]), ([], 1))

    def test_short_chunks_from_heading_split_notes_still_generate(self) -> None:
        self.add("short.md", "# A\n포트는 80\n# B\n상태코드 404")  # chunks well under any minimum length
        self.assertEqual(len(self.features["quiz"].generate(count=1)["items"]), 1)

    def test_question_that_copies_our_instructions_is_rejected(self) -> None:
        self.add()
        self.llm.sequence = ["다음 중 기본키의 특징은? (단, 구체적인 수치나 예시는 사용하지 말라)"]
        result = self.features["quiz"].generate(count=1)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(self.db.stats()["quiz_items"], 0)

    def test_generate_without_material_raises(self) -> None:
        with self.assertRaises(NoMaterialError):
            self.features["quiz"].generate()

    def test_grading_mcq_by_index_or_text(self) -> None:
        self.add()
        quiz = self.features["quiz"]
        qid = quiz.generate(count=1)["items"][0]["id"]
        self.assertFalse(quiz.grade(qid, "0")["correct"])
        self.assertTrue(quiz.grade(qid, "1")["correct"])
        self.assertTrue(quiz.grade(qid, "80")["correct"])
        self.assertEqual(quiz.grade(qid, "0")["correct_answer"], "80")
        self.assertIsNone(quiz.grade(9999, "1"))

    def test_short_answer_exact_judged_and_fail_closed(self) -> None:
        self.add()
        quiz = self.features["quiz"]
        qid = quiz.generate(count=1, kind="short")["items"][0]["id"]
        self.assertTrue(quiz.grade(qid, " http ")["correct"])  # normalized exact match, no LLM call
        self.llm.judge_correct = True
        self.assertTrue(quiz.grade(qid, "하이퍼텍스트 전송 규약")["correct"])
        self.llm.judge_correct = False
        self.assertFalse(quiz.grade(qid, "FTP")["correct"])
        self.llm.fail = True
        self.assertFalse(quiz.grade(qid, "모르겠다")["correct"])
        self.assertFalse(quiz.grade(qid, "")["correct"])

    def test_review_puts_wrong_answers_first(self) -> None:
        self.add()
        quiz = self.features["quiz"]
        ids = [quiz.generate(count=1)["items"][0]["id"] for _ in range(3)]
        quiz.grade(ids[0], "1")   # correct
        quiz.grade(ids[1], "0")   # wrong
        order = [i["id"] for i in quiz.review(limit=3)]
        self.assertEqual(order[0], ids[1])       # last attempt wrong -> first
        self.assertEqual(order[1], ids[2])       # never attempted -> next
        self.assertEqual(order[2], ids[0])       # answered correctly -> last
        quiz.grade(ids[1], "1")                  # now corrected: no longer top priority
        self.assertNotEqual(quiz.review(limit=1)[0]["id"], ids[1])


class SummaryAndProgressTests(Base):
    def test_summary_is_cached_and_refreshable(self) -> None:
        doc = self.add()
        summary = self.features["summary"]
        first = summary.summarize(doc["id"])
        self.assertFalse(first["cached"])
        self.assertEqual(first["glossary"][0]["term"], "HTTP")
        calls = len(self.llm.calls)
        self.assertTrue(summary.summarize(doc["id"])["cached"])
        self.assertEqual(len(self.llm.calls), calls)
        self.assertFalse(summary.summarize(doc["id"], refresh=True)["cached"])
        self.assertIsNone(summary.summarize(9999))

    def test_long_document_uses_map_reduce(self) -> None:
        text = "\n\n".join(f"{i}번째 문단 " + "내용 " * 150 for i in range(30))
        doc = self.add("long.txt", text)
        self.features["summary"].summarize(doc["id"])
        map_calls = [c for c in self.llm.calls if "핵심을 한국어 글머리표" in c[0]["content"]]
        self.assertGreater(len(map_calls), 1)
        self.assertIn("[부분 요약]", self.llm.calls[-1][1]["content"])

    def test_failed_generation_is_not_cached(self) -> None:
        doc = self.add()
        self.llm.summary = {}
        self.llm.chat = lambda messages, json_mode=False: "not json"  # type: ignore[method-assign]
        self.assertIn("만들지 못했습니다", self.features["summary"].summarize(doc["id"])["summary"])
        self.assertIsNone(self.db.get_note(doc["id"], "summary"))

    def test_progress_aggregates_topics_and_flags_weak_ones(self) -> None:
        self.add()
        quiz = self.features["quiz"]
        self.assertEqual(self.features["progress"].report()["attempts"], 0)
        qid = quiz.generate(count=1)["items"][0]["id"]
        quiz.grade(qid, "0")
        quiz.grade(qid, "0")
        quiz.grade(qid, "1")
        report = self.features["progress"].report()
        self.assertEqual(report["attempts"], 3)
        self.assertAlmostEqual(report["accuracy"], 0.333, places=2)
        self.assertEqual(report["weak_topics"], ["HTTP"])


class ApiTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(create_app(self.services))

    def _upload(self, name="net.txt", body="HTTP 기본 포트는 80이다. 웹 요청 규약이다. " * 5):
        return self.client.post("/api/documents", files={"file": (name, body.encode("utf-8"), "text/plain")})

    def test_full_flow(self) -> None:
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        created = self._upload()
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self._upload().status_code, 200)  # duplicate
        doc_id = created.json()["document"]["id"]
        self.assertEqual(len(self.client.get("/api/documents").json()["documents"]), 1)

        response = self.client.post("/api/chat/stream", json={"question": "HTTP 포트는?", "history": [], "mode": "qa"})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([e["type"] for e in events], ["meta", "delta", "delta", "done"])
        self.assertEqual(events[0]["citations"][0]["doc_id"], doc_id)

        gen = self.client.post("/api/quiz/generate", json={"count": 1}).json()
        qid = gen["items"][0]["id"]
        self.assertEqual(self.client.post(f"/api/quiz/{qid}/answer", json={"answer": "1"}).json()["correct"], True)
        self.assertEqual(len(self.client.get("/api/quiz/review").json()["items"]), 1)
        self.assertEqual(self.client.get("/api/progress").json()["attempts"], 1)
        self.assertEqual(self.client.post(f"/api/documents/{doc_id}/summary").json()["summary"], "요약문")
        self.assertEqual(self.client.delete(f"/api/documents/{doc_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/documents/{doc_id}").status_code, 404)

    def test_status_and_feature_listing(self) -> None:
        status = self.client.get("/api/status").json()
        self.assertTrue(status["llm"]["connected"])
        self.assertIn(".pdf", status["extensions"])
        names = {f["name"]: f["chat"] for f in self.client.get("/api/features").json()["features"]}
        self.assertEqual(names, {"qa": True, "code": True, "quiz": False, "summary": False, "progress": False})

    def test_error_mapping(self) -> None:
        self.assertEqual(self._upload("a.exe").status_code, 415)
        self.assertEqual(self._upload("a.txt", "").status_code, 400)
        self.assertEqual(self._upload("a.pdf", "junk").status_code, 422)
        self.assertEqual(self.client.post("/api/quiz/generate", json={"count": 1}).status_code, 422)  # no material
        self.assertEqual(self.client.post("/api/quiz/999/answer", json={"answer": "1"}).status_code, 404)
        self.assertEqual(self.client.post("/api/documents/999/summary").status_code, 404)
        bad_mode = self.client.post("/api/chat/stream", json={"question": "q", "mode": "quiz"})
        self.assertEqual(bad_mode.status_code, 422)
        system_role = self.client.post("/api/chat/stream", json={"question": "q", "history": [{"role": "system", "content": "x"}]})
        self.assertEqual(system_role.status_code, 422)

    def test_llm_failure_during_generation_is_503(self) -> None:
        self._upload()
        self.llm.fail = True
        self.assertEqual(self.client.post("/api/quiz/generate", json={"count": 1}).status_code, 503)

    def test_security_headers_and_remote_mode_requires_api_key(self) -> None:
        self.assertEqual(self.client.get("/api/status").headers["x-frame-options"], "DENY")
        with patch.dict(os.environ, {"ALLOW_REMOTE_ACCESS": "true", "CLASS_API_KEY": ""}):
            self.assertEqual(self.client.get("/api/status").status_code, 403)  # no key configured: fail closed
        with patch.dict(os.environ, {"ALLOW_REMOTE_ACCESS": "true", "CLASS_API_KEY": "k3y"}):
            self.assertEqual(self.client.get("/api/status").status_code, 401)
            self.assertEqual(self.client.get("/api/status", headers={"X-API-Key": "wrong"}).status_code, 401)
            self.assertEqual(self.client.get("/api/status", headers={"X-API-Key": "k3y"}).status_code, 200)
            self.assertEqual(self.client.get("/healthz").status_code, 200)


if __name__ == "__main__":
    unittest.main()

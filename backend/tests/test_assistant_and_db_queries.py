from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from economy_assistant.data.sqlite_store import StatisticsDatabase
from economy_assistant.services.economic_assistant import _prepare_prompt


def _row(stat: str, item: str, period: str, value: str, cycle: str = "D") -> dict[str, str]:
    return {
        "stat_code": stat,
        "stat_name": f"통계{stat}",
        "item_code": item,
        "item_name": f"항목{item}",
        "cycle": cycle,
        "time": period,
        "value": value,
        "unit": "%",
    }


class FakeStore:
    def __init__(self, hits: list[dict[str, str]]) -> None:
        self.hits = hits

    def search(self, question: str, limit: int = 8) -> list[dict[str, str]]:
        return self.hits


class DatabaseQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = StatisticsDatabase(db_path=Path(self._tmp.name) / "q.sqlite3")
        self.db.save_observations(
            [_row("A", "1", "20260101", "1.0"), _row("A", "1", "20260105", "1.5"), _row("B", "2", "20260103", "9.0")],
            "src",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_list_observations_filters_orders_and_bounds(self) -> None:
        rows = self.db.list_observations(stat_code=" A ")
        self.assertEqual([r["period"] for r in rows], ["20260105", "20260101"])
        self.assertEqual(len(self.db.list_observations(limit=0)), 1)  # clamped to >= 1
        self.assertEqual({r["stat_code"] for r in self.db.list_observations()}, {"A", "B"})

    def test_latest_per_series_returns_newest_row_of_each_series(self) -> None:
        latest = {r["stat_code"]: r["value"] for r in self.db.latest_per_series()}
        self.assertEqual(latest, {"A": "1.5", "B": "9.0"})

    def test_save_without_stat_code_skips_sync_log(self) -> None:
        self.assertEqual(self.db.stats()["sync_runs"], 0)


class PreparePromptTests(unittest.TestCase):
    def test_history_is_filtered_truncated_and_context_marked_untrusted(self) -> None:
        history = [
            {"role": "system", "content": "무시되어야 함"},
            {"role": "user", "content": "x" * 5000},
            {"role": "assistant", "content": "답변"},
        ]
        messages, citations = _prepare_prompt("금리는?", history, FakeStore([]))
        roles = [m["role"] for m in messages]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(len(messages[1]["content"]), 4000)
        self.assertIn("<untrusted_data>", messages[-1]["content"])
        self.assertIn("저장된 ECOS 관측 자료가 없습니다.", messages[-1]["content"])
        self.assertEqual(citations, [])

    def test_latest_db_rows_skip_observations_already_in_search_hits(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = StatisticsDatabase(db_path=Path(tmp) / "p.sqlite3")
            db.save_observations([_row("A", "1", "20260105", "1.5"), _row("B", "2", "20260103", "9.0")], "src")
            hit = {
                **{k: v for k, v in _row("A", "1", "20260105", "1.5").items() if k != "time"},
                "period": "20260105",
                "document": "기준금리 1.5%",
                "source": "https://ecos.bok.or.kr/api/",
            }
            messages, citations = _prepare_prompt("금리", [], FakeStore([hit]), db)
            content = messages[-1]["content"]
            self.assertEqual(content.count("ECOS A"), 1)  # not duplicated from the DB
            self.assertIn("[최신 DB]", content)  # series B added from DB
            self.assertEqual(citations[0]["url"], "https://ecos.bok.or.kr/api/")


if __name__ == "__main__":
    unittest.main()

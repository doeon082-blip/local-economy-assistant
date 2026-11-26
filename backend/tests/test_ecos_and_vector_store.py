from __future__ import annotations

import gc
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from economy_assistant.data.ecos_client import EcosError, fetch_many, fetch_series
from economy_assistant.data.sqlite_store import StatisticsDatabase
from economy_assistant.data.vector_store import VectorStore


class EcosAndStorageTests(unittest.TestCase):
    def test_fetch_series_rejects_invalid_calendar_date(self) -> None:
        with self.assertRaises(EcosError):
            fetch_series("722Y001", "D", "20260230", "20260301")

    def test_fetch_series_raises_on_top_level_ecos_result(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}})

        transport = httpx.MockTransport(handler)
        orig_client = httpx.Client

        def fake_client(*args, **kwargs):
            kwargs["transport"] = transport
            return orig_client(*args, **kwargs)

        with patch("economy_assistant.data.ecos_client.httpx.Client", side_effect=fake_client):
            with self.assertRaises(EcosError) as ctx:
                fetch_series("722Y001", "D", "20260101", "20260110")
        self.assertIn("INFO-200", str(ctx.exception))

    def test_fetch_series_paginates_and_redacts_key(self) -> None:
        requested_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            if "/1/1000/" in str(request.url):
                rows = [
                    {
                        "STAT_CODE": "722Y001",
                        "STAT_NAME": "한국은행 기준금리",
                        "ITEM_CODE1": None,
                        "ITEM_NAME1": None,
                        "CYCLE": "D",
                        "TIME": f"202601{i:02d}",
                        "DATA_VALUE": "2.75",
                        "UNIT_NAME": "%",
                    }
                    for i in range(1, 3)
                ]
                return httpx.Response(200, json={"StatisticSearch": {"list_total_count": 1200, "row": rows}})
            return httpx.Response(
                200,
                json={
                    "StatisticSearch": {
                        "list_total_count": 1200,
                        "row": [
                            {
                                "STAT_CODE": "722Y001",
                                "STAT_NAME": "한국은행 기준금리",
                                "ITEM_CODE1": "0101000",
                                "ITEM_NAME1": "기준금리",
                                "CYCLE": "D",
                                "TIME": "20260103",
                                "DATA_VALUE": "2.50",
                                "UNIT_NAME": "%",
                            }
                        ],
                    }
                },
            )

        transport = httpx.MockTransport(handler)
        orig_client = httpx.Client

        def fake_client(*args, **kwargs):
            kwargs["transport"] = transport
            return orig_client(*args, **kwargs)

        with patch.dict(os.environ, {"ECOS_API_KEY": "secret-test-key"}, clear=False):
            with patch("economy_assistant.data.ecos_client.httpx.Client", side_effect=fake_client):
                rows, redacted_url = fetch_series("722Y001", "D", "20260101", "20260110", item_code="0101000", limit=1200)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["item_code"], "0101000")
        self.assertTrue(any("/1001/1200/" in url for url in requested_urls))
        self.assertNotIn("secret-test-key", redacted_url)
        self.assertIn("/REDACTED/", redacted_url)

    def test_fetch_many_runs_concurrently(self) -> None:
        with patch(
            "economy_assistant.data.ecos_client.fetch_series",
            side_effect=lambda stat_code, *args, **kwargs: ([{"stat_code": stat_code}], "https://ecos.bok.or.kr/api/"),
        ):
            results = fetch_many(
                [
                    {"stat_code": "722Y001", "cycle": "D", "start": "20260101", "end": "20260110"},
                    {"stat_code": "731Y001", "cycle": "M", "start": "202601", "end": "202610"},
                ]
            )
        self.assertEqual([spec["stat_code"] for spec, _ in results], ["722Y001", "731Y001"])

    def test_sqlite_and_incremental_vector_store(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            tmp = Path(tmpdir)
            db = StatisticsDatabase(db_path=tmp / "ecos.sqlite3")
            store = VectorStore(persist_dir=tmp / "chroma")
            rows = [
                {
                    "stat_code": "722Y001",
                    "stat_name": "한국은행 기준금리",
                    "item_code": "0101000",
                    "item_name": "기준금리",
                    "cycle": "D",
                    "time": "20260102",
                    "value": "2.75",
                    "unit": "%",
                },
                {
                    "stat_code": "722Y001",
                    "stat_name": "한국은행 기준금리",
                    "item_code": "0101000",
                    "item_name": "기준금리",
                    "cycle": "D",
                    "time": "20260102",
                    "value": "2.50",
                    "unit": "%",
                },
            ]
            try:
                db_saved = db.save_observations(
                    rows,
                    "https://ecos.bok.or.kr/api/",
                    stat_code="722Y001",
                    item_code="0101000",
                    cycle="D",
                    start_period="20260101",
                    end_period="20260110",
                )
                self.assertEqual(db_saved, 1)
                self.assertEqual(db.stats(), {"observations": 1, "sync_runs": 1})
                self.assertEqual(db.latest_per_series()[0]["value"], "2.50")

                embed_calls = 0

                def fake_embed(texts: list[str]) -> list[list[float]]:
                    nonlocal embed_calls
                    embed_calls += 1
                    return [[0.1, 0.2, 0.3] for _ in texts]

                with patch.object(store, "_embed", side_effect=fake_embed):
                    saved_first = store.upsert([rows[1]], "https://ecos.bok.or.kr/api/")
                    saved_second = store.upsert([rows[1]], "https://ecos.bok.or.kr/api/")
                    self.assertEqual(saved_first, 1)
                    self.assertEqual(saved_second, 1)
                    self.assertEqual(embed_calls, 1)  # Unchanged observation skips re-embedding.
                    hits = store.search("기준금리")
                    self.assertEqual(len(hits), 1)
                    self.assertEqual(hits[0]["value"], "2.50")
            finally:
                store.close()
                del store
                gc.collect()


if __name__ == "__main__":
    unittest.main()

"""Immediate SQLite persistence for ECOS statistical observations and sync logs."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from ..config import DB_PATH


class StatisticsDatabase:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    stat_code TEXT NOT NULL,
                    stat_name TEXT NOT NULL,
                    item_code TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    cycle TEXT NOT NULL,
                    period TEXT NOT NULL,
                    value TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    PRIMARY KEY (stat_code, item_code, cycle, period)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stat_code TEXT NOT NULL,
                    item_code TEXT NOT NULL,
                    cycle TEXT NOT NULL,
                    start_period TEXT NOT NULL,
                    end_period TEXT NOT NULL,
                    fetched_count INTEGER NOT NULL,
                    synced_at TEXT NOT NULL
                )
                """
            )

    def save_observations(
        self,
        rows: list[dict[str, str]],
        source_url: str,
        *,
        stat_code: str = "",
        item_code: str = "",
        cycle: str = "",
        start_period: str = "",
        end_period: str = "",
    ) -> int:
        deduped: dict[tuple[str, str, str, str], dict[str, str]] = {}
        for row in rows:
            key = (row["stat_code"], row["item_code"], row["cycle"], row["time"])
            deduped[key] = row

        records = [
            (
                r["stat_code"],
                r["stat_name"],
                r["item_code"],
                r["item_name"],
                r["cycle"],
                r["time"],
                r["value"],
                r["unit"],
                source_url,
            )
            for r in deduped.values()
        ]
        synced_at = datetime.now(timezone.utc).isoformat()

        with closing(self._connect()) as conn, conn:
            if records:
                conn.executemany(
                    """
                    INSERT INTO observations (
                        stat_code, stat_name, item_code, item_name,
                        cycle, period, value, unit, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stat_code, item_code, cycle, period) DO UPDATE SET
                        stat_name = excluded.stat_name,
                        item_name = excluded.item_name,
                        value = excluded.value,
                        unit = excluded.unit,
                        source_url = excluded.source_url
                    """,
                    records,
                )
            if stat_code:
                conn.execute(
                    """
                    INSERT INTO sync_logs (
                        stat_code, item_code, cycle, start_period,
                        end_period, fetched_count, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (stat_code, item_code, cycle, start_period, end_period, len(records), synced_at),
                )
        return len(records)

    def list_observations(self, stat_code: str = "", limit: int = 100) -> list[dict[str, str]]:
        bounded = max(1, min(limit, 500))
        with closing(self._connect()) as conn:
            if stat_code.strip():
                rows = conn.execute(
                    """
                    SELECT stat_code, stat_name, item_code, item_name, cycle, period, value, unit, source_url
                    FROM observations
                    WHERE stat_code = ?
                    ORDER BY period DESC
                    LIMIT ?
                    """,
                    (stat_code.strip(), bounded),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT stat_code, stat_name, item_code, item_name, cycle, period, value, unit, source_url
                    FROM observations
                    ORDER BY stat_code ASC, period DESC
                    LIMIT ?
                    """,
                    (bounded,),
                ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            obs_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            sync_count = conn.execute("SELECT COUNT(*) FROM sync_logs").fetchone()[0]
        return {"observations": int(obs_count), "sync_runs": int(sync_count)}

    def latest_per_series(self, limit: int = 6) -> list[dict[str, str]]:
        bounded = max(1, min(limit, 30))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT o.stat_code, o.stat_name, o.item_code, o.item_name,
                       o.cycle, o.period, o.value, o.unit, o.source_url
                FROM observations o
                JOIN (
                    SELECT stat_code, item_code, cycle, MAX(period) AS max_period
                    FROM observations
                    GROUP BY stat_code, item_code, cycle
                ) latest
                  ON o.stat_code = latest.stat_code
                 AND o.item_code = latest.item_code
                 AND o.cycle = latest.cycle
                 AND o.period = latest.max_period
                ORDER BY o.period DESC, o.stat_code ASC
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return [dict(row) for row in rows]

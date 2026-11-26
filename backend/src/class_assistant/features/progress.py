"""Learning progress derived from quiz attempts (no extra data entry)."""
from __future__ import annotations

from typing import Any

from .base import Feature, register_feature

WEAK_ACCURACY = 0.6  # topics below this accuracy are suggested for review


@register_feature
class ProgressFeature(Feature):
    name = "progress"
    title = "진도·약점"
    description = "주제별 정답률을 집계하고 복습이 필요한 주제를 추천합니다."

    def report(self) -> dict[str, Any]:
        topics = []
        for row in self.services.db.progress_by_topic():
            attempts = int(row["attempts"])
            accuracy = round(int(row["correct"]) / attempts, 3) if attempts else None
            topics.append({"topic": row["topic"], "items": row["items"], "attempts": attempts, "accuracy": accuracy})
        attempts_total = sum(t["attempts"] for t in topics)
        correct_total = sum(int(r["correct"]) for r in self.services.db.progress_by_topic())
        weak = sorted(
            (t for t in topics if t["accuracy"] is not None and t["accuracy"] < WEAK_ACCURACY),
            key=lambda t: (t["accuracy"], -t["attempts"]),
        )
        return {
            "attempts": attempts_total,
            "accuracy": round(correct_total / attempts_total, 3) if attempts_total else None,
            "topics": topics,
            "weak_topics": [t["topic"] for t in weak[:5]],
        }

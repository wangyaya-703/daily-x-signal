from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_x_signal.feishu_bitable import _build_fields
from daily_x_signal.models import Author, Post, Report


class FeishuBitableTests(unittest.TestCase):
    def test_build_fields_contains_expected_summary_values(self) -> None:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        post = Post(
            id="1",
            conversation_id="1",
            created_at=now,
            text="demo",
            url="https://x.com/demo/status/1",
            author=Author(handle="demo"),
            summary_bullets=["要点"],
            why_it_matters="值得关注",
        )
        report = Report(
            generated_at=now,
            window_start=now,
            window_end=now,
            mode="all_following",
            top_posts=[post],
            must_read=post,
            watchlist_authors=[],
            overview_bullets=["今天主线是 AI 编程。"],
            section_stats={"top_topics": ["AI 编程"], "high_fit_posts": 1},
            metadata={
                "candidate_count": 12,
                "author_count": 5,
                "following_status": {"reason": "following 已完整同步"},
            },
        )

        fields = _build_fields(
            report,
            {
                "fields": {
                    "digest_date": "Digest Date",
                    "must_read": "Must Read",
                    "overview": "Overview",
                    "top_topics": "Top Topics",
                }
            },
        )

        self.assertEqual(fields["Digest Date"], now.date().isoformat())
        self.assertIn("@demo", fields["Must Read"])
        self.assertIn("今天主线", fields["Overview"])
        self.assertEqual(fields["Top Topics"], "AI 编程")


if __name__ == "__main__":
    unittest.main()

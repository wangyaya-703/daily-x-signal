from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_x_signal.feishu_bitable import bitable_app_url, build_post_rows
from daily_x_signal.models import Author, Post, Report


class FeishuBitableTests(unittest.TestCase):
    def test_bitable_app_url_uses_app_token(self) -> None:
        url = bitable_app_url({"outputs": {"feishu_bitable": {"app_token": "app123"}}})
        self.assertEqual(url, "https://bytedance.larkoffice.com/base/app123")

    def test_build_post_rows_contains_expected_tracking_fields(self) -> None:
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
            overview_bullets=[],
            section_stats={},
            metadata={},
        )

        rows = build_post_rows(
            report,
            {
                "fields": {
                    "post_id": "Post ID",
                    "digest_date": "Digest Date",
                    "priority": "Priority",
                    "priority_score": "Priority Score",
                    "original_url": "Original URL",
                    "summary": "Summary",
                    "published_at": "Published At",
                    "topics": "Topics",
                }
            },
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["Post ID"], "1")
        self.assertEqual(row["Digest Date"], now.date().isoformat())
        self.assertEqual(row["Priority Score"], 0.0)
        self.assertEqual(row["Original URL"]["link"], "https://x.com/demo/status/1")
        self.assertIn("值得关注", row["Summary"])
        self.assertEqual(row["Topics"], "")


if __name__ == "__main__":
    unittest.main()

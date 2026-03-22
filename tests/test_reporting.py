from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_x_signal.models import Author, Post
from daily_x_signal.report import build_report_overview


class ReportingTests(unittest.TestCase):
    def test_build_report_overview_generates_stats_without_llm(self) -> None:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        post = Post(
            id="1",
            conversation_id="1",
            created_at=now,
            text="codex benchmark workflow",
            url="https://x.com/demo/status/1",
            author=Author(handle="demo"),
        )
        post.tags = ["ai_coding", "fit:high", "signal:high"]
        post.scores = {"personal_fit": 3.4, "priority": 10.0}

        overview, stats = build_report_overview(
            [post],
            {"summary_bullets": ["当前偏好最强的主题是：AI 编程。"]},
            [],
            {"reason": "following 列表已达到当前配置下的完整性要求。"},
        )

        self.assertTrue(overview)
        self.assertIn("AI 编程", overview[0])
        self.assertEqual(stats["high_fit_posts"], 1)
        self.assertEqual(stats["high_signal_posts"], 1)
        self.assertEqual(stats["top_authors"], ["demo"])
        self.assertEqual(stats["top_topics"], ["AI 编程 / Coding Agent / 工作流"])


if __name__ == "__main__":
    unittest.main()

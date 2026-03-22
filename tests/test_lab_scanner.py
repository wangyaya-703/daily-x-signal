"""scanner.py 单元测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from daily_x_signal.lab.scanner import (
    ACTIONABLE_TAGS,
    _score_post,
    find_latest_brief,
    load_brief,
    scan_for_candidates,
)


def _make_post(
    tags=None,
    summary_bullets=None,
    why_it_matters="",
    text="",
    priority_label="C",
    topic_scores=None,
    post_id="123",
):
    return {
        "id": post_id,
        "tags": tags or [],
        "summary_bullets": summary_bullets or [],
        "why_it_matters": why_it_matters,
        "text": text,
        "priority_label": priority_label,
        "topic_scores": topic_scores or {},
        "url": "https://x.com/test/status/123",
        "author": {"handle": "testuser", "name": "Test"},
    }


class TestScorePost:
    def test_no_signals_returns_none(self):
        post = _make_post(text="hello world", tags=["freshness:high"])
        assert _score_post(post) is None

    def test_actionable_tag_gives_3(self):
        post = _make_post(tags=["开源", "freshness:high"], text="some text about 安装步骤")
        result = _score_post(post)
        assert result is not None
        assert result["actionable_score"] >= 3

    def test_keywords_give_2(self):
        post = _make_post(
            summary_bullets=["开源框架可以直接安装"],
            text="use pip install to setup",
        )
        result = _score_post(post)
        assert result is not None
        assert result["actionable_score"] >= 2

    def test_priority_s_gives_1(self):
        post = _make_post(
            priority_label="S",
            summary_bullets=["开源项目可 clone 部署"],
        )
        result = _score_post(post)
        assert result is not None
        # S 级 (+1) + 关键词 (+2) = 3
        assert result["actionable_score"] >= 3

    def test_high_topic_score_gives_1(self):
        post = _make_post(
            topic_scores={"ai_coding": 4.5},
            summary_bullets=["提供安装教程和代码示例"],
        )
        result = _score_post(post)
        assert result is not None
        assert result["actionable_score"] >= 3  # 关键词 +2, topic +1

    def test_all_signals_max_score(self):
        post = _make_post(
            tags=["开源", "Agent框架"],
            summary_bullets=["开源框架 pip install 即用"],
            priority_label="S",
            topic_scores={"ai_coding": 5.0, "agent_frameworks": 4.0},
        )
        result = _score_post(post)
        assert result is not None
        assert result["actionable_score"] == 7  # 3+2+1+1

    def test_actionable_tags_extracted(self):
        post = _make_post(
            tags=["开源", "freshness:high", "SDK", "signal:medium"],
            text="安装教程",
        )
        result = _score_post(post)
        assert result is not None
        assert set(result["actionable_tags"]) == {"开源", "SDK"}


class TestScanForCandidates:
    def test_deduplicates_must_read_and_top_posts(self):
        post = _make_post(
            post_id="same",
            tags=["开源"],
            summary_bullets=["开源框架安装教程"],
            priority_label="S",
        )
        brief = {"must_read": post, "top_posts": [post]}
        candidates = scan_for_candidates(brief)
        assert len(candidates) == 1

    def test_sorted_by_score_desc(self):
        high = _make_post(
            post_id="high",
            tags=["开源", "Agent框架"],
            summary_bullets=["开源框架安装"],
            priority_label="S",
            topic_scores={"ai_coding": 5.0},
        )
        low = _make_post(
            post_id="low",
            summary_bullets=["开源工具安装"],
        )
        brief = {"top_posts": [low, high]}
        candidates = scan_for_candidates(brief)
        assert len(candidates) == 2
        assert candidates[0]["actionable_score"] >= candidates[1]["actionable_score"]

    def test_empty_brief(self):
        assert scan_for_candidates({}) == []
        assert scan_for_candidates({"top_posts": []}) == []


class TestFindLatestBrief:
    def test_finds_latest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "daily-brief-2026-03-18.json").write_text("{}")
            (d / "daily-brief-2026-03-20.json").write_text("{}")
            (d / "daily-brief-2026-03-19.json").write_text("{}")
            result = find_latest_brief(d)
            assert result is not None
            assert "2026-03-20" in result.name

    def test_excludes_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            sub = d / "feishu-preview"
            sub.mkdir()
            (sub / "daily-brief-2026-03-20.json").write_text("{}")
            (d / "daily-brief-2026-03-19.json").write_text("{}")
            result = find_latest_brief(d)
            assert result is not None
            assert "2026-03-19" in result.name

    def test_empty_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert find_latest_brief(Path(tmpdir)) is None


class TestLoadBrief:
    def test_loads_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"key": "value"}, f)
            f.flush()
            result = load_brief(Path(f.name))
            assert result == {"key": "value"}

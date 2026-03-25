"""scoring.py 测试：营销降权、disliked_keywords 惩罚、纯转推降权。"""
from __future__ import annotations

from datetime import datetime

import pytest

from daily_x_signal.models import Author, Post
from daily_x_signal.scoring import score_substance, penalty


def _make_post(text: str = "", **kwargs) -> Post:
    defaults = dict(
        id="1",
        conversation_id="1",
        created_at=datetime(2026, 3, 24),
        url="https://x.com/u/status/1",
        author=Author(handle="test"),
    )
    defaults.update(kwargs)
    return Post(text=text, **defaults)


def _base_config(**profile_overrides) -> dict:
    config = {
        "topics": {},
        "ranking": {
            "weights": {"topic_relevance": 0.35, "substance": 0.35, "social_signal": 0.2, "author_signal": 0.1},
            "penalties": {"too_short": 1.0, "pure_link": 1.5, "obvious_noise": 2.0},
        },
        "profile": {"disliked_keywords": []},
        "personalization": {"ranking_weight": 0.15},
    }
    config["profile"].update(profile_overrides)
    return config


class TestMarketingPenalty:
    """营销内容在 score_substance 中被降权。"""

    def test_marketing_text_gets_lower_substance(self):
        """含营销关键词的帖子干货分更低。"""
        normal = _make_post("This is a great new AI framework for building agents with code examples and benchmarks")
        marketing = _make_post("This is a great new AI framework! Sign up now for exclusive offer and get early bird pricing!")
        normal_score = score_substance(normal)
        marketing_score = score_substance(marketing)
        assert marketing_score < normal_score, f"营销帖 {marketing_score} 应低于正常帖 {normal_score}"

    def test_chinese_marketing_detected(self):
        """中文营销关键词也被识别。"""
        post = _make_post("限时免费领 AI 编程工具大礼包，优惠仅限今天！")
        score = score_substance(post)
        # 短文本 + 营销关键词，分数应该很低
        assert score < 1.0


class TestRetweetPenalty:
    """纯转推降权。"""

    def test_retweet_gets_lower_substance(self):
        """is_retweet=True 的帖子干货分更低。"""
        original = _make_post("Some interesting content about AI agents")
        retweet = _make_post("Some interesting content about AI agents", is_retweet=True)
        assert score_substance(retweet) < score_substance(original)


class TestDislikedKeywords:
    """用户配置的 disliked_keywords 在 penalty() 中生效。"""

    def test_disliked_keyword_adds_penalty(self):
        """命中 disliked_keyword 时增加惩罚分。"""
        post = _make_post("I think crypto is the future of finance")
        config_with = _base_config(disliked_keywords=["crypto"])
        config_without = _base_config(disliked_keywords=[])
        penalty_with = penalty(post, config_with, topic_relevance=2.0, substance=2.0)
        penalty_without = penalty(post, config_without, topic_relevance=2.0, substance=2.0)
        assert penalty_with > penalty_without

    def test_multiple_disliked_keywords_stack(self):
        """多个 disliked_keywords 命中时惩罚叠加。"""
        post = _make_post("Buy crypto NFT tokens now!")
        config = _base_config(disliked_keywords=["crypto", "nft"])
        pen = penalty(post, config, topic_relevance=2.0, substance=2.0)
        # 两个关键词各 1.5 = 3.0 额外惩罚
        assert pen >= 3.0


class TestViralLowInfoPenalty:
    """高互动但低信息量的帖子被额外惩罚。"""

    def test_viral_but_irrelevant_gets_penalty(self):
        """高点赞但低主题相关 + 低干货的帖子被惩罚。"""
        post = _make_post("lol", like_count=50000, retweet_count=10000)
        config = _base_config()
        pen = penalty(post, config, topic_relevance=0.0, substance=0.5)
        # 应该触发"高互动低信息"惩罚 + too_short + 其他
        assert pen >= 2.0

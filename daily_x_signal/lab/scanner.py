"""日报候选识别：扫描最新 brief，按规则评分筛选实操性强的帖子。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# 实操标签集
ACTIONABLE_TAGS = {"开源", "Agent框架", "SDK", "教程", "工具"}

# 摘要/正文中的实操关键词
ACTIONABLE_KEYWORDS = (
    "开源",
    "安装",
    "install",
    "pip",
    "npm",
    "clone",
    "代码",
    "code",
    "框架",
    "framework",
    "sdk",
    "教程",
    "tutorial",
    "步骤",
    "工具",
    "tool",
    "repo",
    "github",
    "setup",
    "配置",
    "部署",
    "deploy",
)


def find_latest_brief(output_dir: Path) -> Path | None:
    """在 output_dir 中找到最新的 daily-brief-*.json 文件。"""
    candidates = sorted(output_dir.glob("daily-brief-*.json"), reverse=True)
    # 排除子目录中的文件（如 feishu-preview/）
    candidates = [p for p in candidates if p.parent == output_dir]
    return candidates[0] if candidates else None


def load_brief(json_path: Path) -> dict:
    """加载 brief JSON 文件。"""
    import json

    with json_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def scan_for_candidates(brief: dict) -> list[dict]:
    """扫描 must_read + top_posts，返回实操候选列表。

    每个 candidate: {post, actionable_score, actionable_tags, reason}
    """
    candidates: list[dict] = []
    seen_ids: set[str] = set()

    # 收集所有待评估的帖子
    posts_to_check: list[dict] = []
    must_read = brief.get("must_read")
    if must_read:
        posts_to_check.append(must_read)

    for post in brief.get("top_posts", []):
        posts_to_check.append(post)

    for post in posts_to_check:
        post_id = post.get("id", "")
        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)

        result = _score_post(post)
        if result is not None:
            candidates.append(result)

    # 按分数降序排列
    candidates.sort(key=lambda c: c["actionable_score"], reverse=True)
    return candidates


def _score_post(post: dict, min_score: int = 2) -> dict[str, Any] | None:
    """评估单个帖子的实操性分数。返回 None 表示不够格。

    评分规则（叠加整数分，阈值 >= min_score）：
    - tags 含实操标签: +3
    - 摘要含实操关键词: +2
    - priority_label = S 或 A: +1
    - topic_scores 中 ai_coding 或 agent_frameworks > 3.0: +1
    """
    score = 0
    matched_tags: list[str] = []
    reasons: list[str] = []

    # 1. tags 含实操标签
    tags = post.get("tags", [])
    for tag in tags:
        # 去掉 freshness:/signal: 等前缀标签
        if ":" in tag:
            continue
        if tag in ACTIONABLE_TAGS:
            matched_tags.append(tag)
    if matched_tags:
        score += 3
        reasons.append(f"标签匹配: {', '.join(matched_tags)}")

    # 2. 摘要/正文含实操关键词
    text_to_check = " ".join([
        " ".join(post.get("summary_bullets", [])),
        post.get("why_it_matters", ""),
        post.get("text", ""),
    ]).lower()
    matched_keywords = [kw for kw in ACTIONABLE_KEYWORDS if kw.lower() in text_to_check]
    if matched_keywords:
        score += 2
        reasons.append(f"关键词匹配: {', '.join(matched_keywords[:5])}")

    # 3. priority_label = S 或 A
    priority = post.get("priority_label", "")
    if priority in ("S", "A"):
        score += 1
        reasons.append(f"优先级: {priority}")

    # 4. topic_scores 中 ai_coding 或 agent_frameworks > 3.0
    topic_scores = post.get("topic_scores", {})
    high_topics = []
    for topic_key in ("ai_coding", "agent_frameworks"):
        topic_val = topic_scores.get(topic_key, 0)
        if isinstance(topic_val, (int, float)) and topic_val > 3.0:
            high_topics.append(f"{topic_key}={topic_val:.1f}")
    if high_topics:
        score += 1
        reasons.append(f"高分主题: {', '.join(high_topics)}")

    if score < min_score:
        return None

    return {
        "post": post,
        "actionable_score": score,
        "actionable_tags": matched_tags,
        "reason": "; ".join(reasons),
    }

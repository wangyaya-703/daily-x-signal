from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .collector import build_signal_snapshot, extract_referenced_handles
from .models import Post


NOISE_PATTERNS = (
    "抽奖",
    "giveaway",
    "转发",
    "关注并",
    "follow & repost",
    "amazon gift",
    "旅行券",
    "woke",
    "anal sphincter",
)

SUBSTANCE_HINTS = (
    "repo",
    "github",
    "paper",
    "arxiv",
    "benchmark",
    "dataset",
    "agent",
    "framework",
    "skill",
    "workflow",
    "mcp",
    "模型",
    "论文",
    "开源",
    "总结",
    "教程",
    "步骤",
    "方法",
)


def score_topics(post: Post, config: dict[str, Any]) -> dict[str, float]:
    text = post.primary_text.lower()
    scores: dict[str, float] = {}
    for topic_name, topic_cfg in config["topics"].items():
        score = 0.0
        for keyword in topic_cfg.get("keywords", []):
            occurrences = text.count(str(keyword).lower())
            if occurrences:
                score += occurrences * float(topic_cfg.get("weight", 1.0))
        if score:
            scores[topic_name] = score
    post.topic_scores = scores
    return scores


def score_substance(post: Post) -> float:
    text = post.primary_text
    lowered = text.lower()
    score = min(len(text) / 280.0, 2.0) * 0.8
    if "\n" in text:
        score += 0.4
    if any(hint in lowered for hint in SUBSTANCE_HINTS):
        score += 1.2
    if re.search(r"https?://", text):
        score += 0.4
    if re.search(r"(^|\n)\s*(\d+[.)、]|[-*•])\s+", text):
        score += 0.5
    if "```" in text:
        score += 0.4
    if post.is_quote:
        score += 0.3
    if post.is_reply and post.like_count >= 100:
        score += 0.5
    return score


def score_social_signal(post: Post) -> float:
    snapshot = build_signal_snapshot(post)
    raw = snapshot["engagement_log"] + math.log1p(post.view_count) * 0.3
    return raw


def score_author_signal(post: Post, author_stats: dict[str, Any]) -> float:
    stats = author_stats.get(post.author.handle, {})
    return (
        float(stats.get("selected_runs", 0)) * 0.4
        + float(stats.get("avg_priority", 0.0)) * 0.6
    )


def penalty(post: Post, config: dict[str, Any], topic_relevance: float, substance: float) -> float:
    penalties = config["ranking"]["penalties"]
    text = post.primary_text.strip()
    lowered = text.lower()
    score = 0.0
    if len(text) < 50:
        score += float(penalties.get("too_short", 0.0))
    if text.startswith("http") or text == "":
        score += float(penalties.get("pure_link", 0.0))
    if any(pattern in lowered for pattern in NOISE_PATTERNS):
        score += float(penalties.get("obvious_noise", 0.0))
    if topic_relevance <= 1.0 and len(text) < 140:
        score += 1.6
    if topic_relevance <= 1.0 and substance < 1.5:
        score += 1.4
    if "\n" not in text and len(text) < 100 and not re.search(r"https?://", text):
        score += 0.8
    return score


def rank_posts(posts: list[Post], config: dict[str, Any], author_stats: dict[str, Any]) -> list[Post]:
    weights = config["ranking"]["weights"]
    for post in posts:
        topic_scores = score_topics(post, config)
        topic_relevance = sum(topic_scores.values())
        substance = score_substance(post)
        social_signal = score_social_signal(post)
        author_signal = score_author_signal(post, author_stats)
        total = (
            float(weights["topic_relevance"]) * topic_relevance
            + float(weights["substance"]) * substance
            + float(weights["social_signal"]) * social_signal
            + float(weights["author_signal"]) * author_signal
            - penalty(post, config, topic_relevance, substance)
        )
        post.scores = {
            "topic_relevance": topic_relevance,
            "substance": substance,
            "social_signal": social_signal,
            "author_signal": author_signal,
            "priority": total,
        }
        post.tags = [tag for tag, value in sorted(topic_scores.items(), key=lambda item: item[1], reverse=True) if value > 0][:3]
    ranked = sorted(posts, key=lambda p: p.scores["priority"], reverse=True)
    for idx, post in enumerate(ranked):
        post.priority_label = priority_label_for_rank(idx)
    return ranked


def priority_label_for_rank(index: int) -> str:
    if index == 0:
        return "S"
    if index <= 2:
        return "A"
    if index <= 5:
        return "B"
    return "C"


def suggested_authors(posts: list[Post], author_stats: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    handle_counter: Counter[str] = Counter()
    reasons: dict[str, list[str]] = {}
    known_authors = {post.author.handle for post in posts}
    for post in posts:
        for handle in extract_referenced_handles(post.primary_text):
            if handle == post.author.handle:
                continue
            handle_counter[handle] += 1
            reasons.setdefault(handle, []).append(post.url)
    ranked: list[dict[str, Any]] = []
    for handle, mentions in handle_counter.most_common(limit):
        if handle in known_authors:
            continue
        ranked.append(
            {
                "handle": handle,
                "reason": f"在 {mentions} 条高优先级帖子里被重点提到，值得补进观察名单。",
                "source_posts": reasons.get(handle, [])[:3],
            }
        )
    if ranked:
        return ranked

    backups = sorted(
        (
            {
                "handle": handle,
                "reason": "在多次日报中持续进入高位，适合纳入重点作者池。",
                "source_posts": [],
                "score": stats.get("avg_priority", 0.0),
            }
            for handle, stats in author_stats.items()
        ),
        key=lambda item: item["score"],
        reverse=True,
    )
    return backups[:limit]

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from .models import Author, Post


EN_TOKEN_RE = re.compile(r"[a-z][a-z0-9_./+-]{2,31}")
ZH_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")
URL_RE = re.compile(r"https?://\S+")

STOPWORDS = {
    "about",
    "after",
    "agentic",
    "agents",
    "also",
    "amid",
    "been",
    "between",
    "build",
    "building",
    "built",
    "code",
    "coding",
    "cofounder",
    "daily",
    "demo",
    "developer",
    "engineer",
    "engineering",
    "follow",
    "founder",
    "from",
    "github",
    "good",
    "have",
    "https",
    "into",
    "just",
    "like",
    "make",
    "more",
    "news",
    "open",
    "opensource",
    "product",
    "python",
    "repo",
    "sharing",
    "some",
    "team",
    "that",
    "their",
    "this",
    "thread",
    "today",
    "tool",
    "tools",
    "using",
    "with",
    "workflow",
    "工作流",
    "开源",
    "整理",
    "项目",
}

TOPIC_META = {
    "ai_coding": {
        "label": "AI 编程 / Coding Agent / 工作流",
        "description": "更偏代码生成、工程效率、MCP、自动化工作流。",
        "seed_keywords": ["codex", "claude code", "cursor", "mcp", "coding agent", "workflow"],
    },
    "agent_frameworks": {
        "label": "Agent 框架 / 多智能体 / 编排",
        "description": "更偏 agent 架构、多智能体协作、工具调用和 orchestration。",
        "seed_keywords": ["agent", "multi agent", "tool calling", "orchestration", "langgraph", "autogen"],
    },
    "model_releases": {
        "label": "新模型 / 新功能 / 产品发布",
        "description": "更偏新模型发布、产品更新、功能 launch、demo 和生态动态。",
        "seed_keywords": ["release", "launch", "feature", "product update", "demo", "benchmark"],
    },
    "papers_algorithms": {
        "label": "模型研究 / 论文 / Benchmark",
        "description": "更偏论文、算法、评测、benchmark 和研究进展。",
        "seed_keywords": ["paper", "arxiv", "benchmark", "research", "reasoning", "algorithm"],
    },
}


def build_interest_profile(config: dict[str, Any], authors: list[Author], history: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("personalization", {})
    if not bool(cfg.get("enabled", True)):
        return {
            "enabled": False,
            "topic_weights": {},
            "top_topics": [],
            "keywords": [],
            "trusted_authors": {},
            "summary_bullets": [],
        }

    topic_scores: defaultdict[str, float] = defaultdict(float)
    keyword_scores: Counter[str] = Counter()
    trusted_authors = _trusted_authors_from_history(history)

    manual_topics = [str(item).strip() for item in config.get("profile", {}).get("preferred_topics", []) if str(item).strip()]
    manual_keywords = [str(item).strip().lower() for item in config.get("profile", {}).get("interest_keywords", []) if str(item).strip()]
    disliked_keywords = [str(item).strip().lower() for item in config.get("profile", {}).get("disliked_keywords", []) if str(item).strip()]

    for topic in manual_topics:
        topic_scores[topic] += 6.0
    for keyword in manual_keywords:
        keyword_scores[keyword] += 6.0
        _boost_topics_for_keyword(keyword, config, topic_scores, boost=2.0)

    analysis_limit = int(cfg.get("author_profile_sample_limit", 200))
    for author in authors[:analysis_limit]:
        text = " ".join(part for part in [author.name, author.description] if part).strip()
        if not text:
            continue
        _accumulate_topics(text, config, topic_scores, scale=0.45)
        keyword_scores.update(_extract_keywords(text))

    history_profile = history.get("interest_profile", {})
    for topic, value in history_profile.get("topic_hits", {}).items():
        topic_scores[str(topic)] += float(value) * 1.5
    for keyword, value in history_profile.get("keyword_hits", {}).items():
        cleaned = str(keyword).strip().lower()
        if cleaned:
            keyword_scores[cleaned] += float(value) * 1.2

    for keyword in disliked_keywords:
        if keyword in keyword_scores:
            del keyword_scores[keyword]

    top_topics = [
        topic
        for topic, score in sorted(topic_scores.items(), key=lambda item: item[1], reverse=True)
        if score > 0
    ][:5]
    topic_weights = _normalize_scores(topic_scores, min_score=0.2)

    ranked_keywords = [
        keyword
        for keyword, _score in keyword_scores.most_common(int(cfg.get("interest_keyword_limit", 12)))
        if keyword not in disliked_keywords
    ]
    summary_bullets = _build_profile_summary(top_topics, ranked_keywords, trusted_authors)
    return {
        "enabled": True,
        "topic_weights": topic_weights,
        "top_topics": top_topics,
        "keywords": ranked_keywords,
        "trusted_authors": trusted_authors,
        "summary_bullets": summary_bullets,
    }


def score_personal_fit(post: Post, interest_profile: dict[str, Any]) -> float:
    if not interest_profile.get("enabled", True):
        return 0.0

    topic_weights = interest_profile.get("topic_weights", {})
    topic_score = 0.0
    for topic, value in post.topic_scores.items():
        topic_score += float(value) * float(topic_weights.get(topic, 0.0))

    lowered = post.primary_text.lower()
    keyword_matches = 0
    for keyword in interest_profile.get("keywords", [])[:12]:
        if keyword and keyword in lowered:
            keyword_matches += 1
    keyword_score = min(keyword_matches * 0.35, 1.4)

    affinity = interest_profile.get("trusted_authors", {}).get(post.author.handle, {})
    author_score = min(float(affinity.get("selected_runs", 0)) * 0.18 + float(affinity.get("avg_priority", 0.0)) * 0.03, 1.8)
    return topic_score + keyword_score + author_score


def derive_personalized_tags(post: Post, interest_profile: dict[str, Any], author_stats: dict[str, Any]) -> list[str]:
    tags: list[str] = [
        tag
        for tag, value in sorted(post.topic_scores.items(), key=lambda item: item[1], reverse=True)
        if value > 0
    ][:3]

    personal_score = float(post.scores.get("personal_fit", 0.0))
    if personal_score >= 3.0:
        tags.append("fit:high")
    elif personal_score >= 1.2:
        tags.append("fit:medium")
    else:
        tags.append("fit:low")

    author_signal = author_stats.get(post.author.handle, {})
    if float(author_signal.get("selected_runs", 0)) >= 3 or float(author_signal.get("avg_priority", 0.0)) >= 8:
        tags.append("author:trusted")

    text = post.primary_text
    if len(post.thread_posts) >= 3 or text.count("\n") >= 3:
        tags.append("format:thread")
    elif post.is_reply:
        tags.append("format:reply")
    elif post.is_quote:
        tags.append("format:quote")
    elif URL_RE.search(text):
        tags.append("format:link")

    engagement = post.like_count + 2 * post.retweet_count + 2 * post.quote_count + int(post.bookmark_count * 1.5)
    if engagement >= 400:
        tags.append("signal:high")
    elif engagement >= 80:
        tags.append("signal:medium")
    else:
        tags.append("signal:low")

    age_hours = max((datetime.now(post.created_at.tzinfo) - post.created_at).total_seconds() / 3600.0, 0.0)
    if age_hours <= 6:
        tags.append("freshness:high")
    elif age_hours <= 18:
        tags.append("freshness:medium")
    else:
        tags.append("freshness:low")

    return _dedupe(tags)


def snapshot_following_status(
    config: dict[str, Any],
    authors: list[Author],
    payload_meta: dict[str, Any] | None = None,
    viewer_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload_meta = payload_meta or {}
    x_cfg = config.get("x", {})
    expected_following = x_cfg.get("expected_following_count")
    if expected_following in (None, "") and viewer_profile:
        expected_following = viewer_profile.get("followingCount")
    expected = int(expected_following or 0)
    synced_count = len(authors)
    has_more = bool(payload_meta.get("has_more", False))
    max_pages = int(x_cfg.get("following_sync_max_pages", 0) or 0)
    warn_ratio = float(x_cfg.get("following_completion_ratio_warn_threshold", 0.85))
    confirmed = bool(x_cfg.get("following_count_confirmed", False))
    expected_explicit_zero = str(expected_following).strip() == "0"

    completion_ratio = round(synced_count / expected, 4) if expected > 0 else None
    is_complete = not has_more and (expected == 0 or synced_count >= max(int(expected * warn_ratio), 1))
    needs_confirmation = bool(x_cfg.get("require_following_confirmation", True)) and not confirmed
    reasons: list[str] = []
    if synced_count == 0 and not (expected_explicit_zero and confirmed and not has_more):
        is_complete = False
        reasons.append("当前未同步到任何 following，建议检查 viewer 配置或 X 登录态。")
    if has_more:
        reasons.append(f"当前 following 同步在 max_pages={max_pages} 截断，仍有下一页。")
    if expected > 0 and synced_count < expected:
        reasons.append(f"当前只同步到 {synced_count}/{expected} 个关注对象。")
    if needs_confirmation:
        reasons.append("请在 config/local.yaml 明确填写 expected_following_count 并确认 following_count_confirmed。")
    if not reasons:
        reasons.append("following 列表已达到当前配置下的完整性要求。")
    return {
        "synced_count": synced_count,
        "expected_following_count": expected if expected > 0 or expected_explicit_zero else None,
        "completion_ratio": completion_ratio,
        "has_more": has_more,
        "is_complete": is_complete,
        "needs_confirmation": needs_confirmation,
        "reason": " ".join(reasons),
    }


def topic_labels(topics: list[str]) -> list[str]:
    return [TOPIC_META.get(topic, {}).get("label", topic) for topic in topics]


def topic_descriptions(topics: list[str]) -> list[str]:
    return [TOPIC_META.get(topic, {}).get("description", "") for topic in topics]


def topic_seed_keywords(topics: list[str]) -> list[str]:
    results: list[str] = []
    for topic in topics:
        results.extend(TOPIC_META.get(topic, {}).get("seed_keywords", []))
    return _dedupe(results)


def extract_keywords_for_history(text: str) -> Counter[str]:
    return _extract_keywords(text)


def _accumulate_topics(text: str, config: dict[str, Any], scores: defaultdict[str, float], scale: float) -> None:
    lowered = text.lower()
    for topic, topic_cfg in config.get("topics", {}).items():
        topic_score = 0.0
        for keyword in topic_cfg.get("keywords", []):
            occurrences = lowered.count(str(keyword).lower())
            if occurrences:
                topic_score += occurrences * float(topic_cfg.get("weight", 1.0))
        if topic_score > 0:
            scores[str(topic)] += topic_score * scale


def _boost_topics_for_keyword(keyword: str, config: dict[str, Any], scores: defaultdict[str, float], boost: float) -> None:
    lowered = keyword.lower()
    for topic, topic_cfg in config.get("topics", {}).items():
        if any(lowered in str(candidate).lower() or str(candidate).lower() in lowered for candidate in topic_cfg.get("keywords", [])):
            scores[str(topic)] += boost


def _normalize_scores(scores: dict[str, float], min_score: float) -> dict[str, float]:
    positive = {key: value for key, value in scores.items() if value > 0}
    if not positive:
        return {}
    max_value = max(positive.values())
    return {key: round(max(value / max_value, min_score), 4) for key, value in positive.items()}


def _build_profile_summary(top_topics: list[str], keywords: list[str], trusted_authors: dict[str, Any]) -> list[str]:
    bullets: list[str] = []
    if top_topics:
        bullets.append(f"当前偏好最强的主题是：{'、'.join(topic_labels(top_topics[:3]))}。")
    if keywords:
        bullets.append(f"从关注简介和历史日报里提炼出的兴趣关键词：{'、'.join(keywords[:6])}。")
    if trusted_authors:
        handles = list(trusted_authors.keys())[:4]
        bullets.append(f"历史上命中率较高的作者包括：{'、'.join(f'@{handle}' for handle in handles)}。")
    return bullets[:3]


def _trusted_authors_from_history(history: dict[str, Any]) -> dict[str, dict[str, float]]:
    trusted: dict[str, dict[str, float]] = {}
    for handle, stats in history.get("authors", {}).items():
        selected_runs = float(stats.get("selected_runs", 0))
        avg_priority = float(stats.get("avg_priority", 0.0))
        if selected_runs >= 2 or avg_priority >= 8:
            trusted[str(handle)] = {
                "selected_runs": selected_runs,
                "avg_priority": avg_priority,
            }
    return dict(sorted(trusted.items(), key=lambda item: (item[1]["selected_runs"], item[1]["avg_priority"]), reverse=True)[:12])


def _extract_keywords(text: str) -> Counter[str]:
    lowered = URL_RE.sub(" ", text.lower())
    counter: Counter[str] = Counter()
    for token in EN_TOKEN_RE.findall(lowered):
        cleaned = token.strip("._-/+")
        if len(cleaned) < 3 or cleaned in STOPWORDS:
            continue
        counter[cleaned] += 1
    for token in ZH_TOKEN_RE.findall(text):
        if token in STOPWORDS:
            continue
        counter[token] += 1
    return counter


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped

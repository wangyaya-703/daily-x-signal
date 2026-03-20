from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Post
from .personalization import extract_keywords_for_history


def load_history(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"runs": [], "authors": {}}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_history(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def author_stats_from_history(history: dict[str, Any]) -> dict[str, Any]:
    return history.get("authors", {})


def update_history(
    history: dict[str, Any],
    selected_posts: list[Post],
    generated_at: datetime,
) -> dict[str, Any]:
    authors = defaultdict(lambda: {"selected_runs": 0, "priority_sum": 0.0, "topic_sum": 0.0, "signal_sum": 0.0})
    interest_profile = history.setdefault("interest_profile", {"topic_hits": {}, "tag_hits": {}, "keyword_hits": {}, "author_hits": {}})
    for handle, stats in history.get("authors", {}).items():
        authors[handle].update(stats)
    for post in selected_posts:
        stats = authors[post.author.handle]
        stats["selected_runs"] += 1
        stats["priority_sum"] += float(post.scores.get("priority", 0.0))
        stats["topic_sum"] += float(post.scores.get("topic_relevance", 0.0))
        stats["signal_sum"] += float(post.scores.get("social_signal", 0.0))
        stats["avg_priority"] = stats["priority_sum"] / stats["selected_runs"]
        stats["avg_topic_relevance"] = stats["topic_sum"] / stats["selected_runs"]
        stats["avg_signal"] = stats["signal_sum"] / stats["selected_runs"]

        author_hits = interest_profile.setdefault("author_hits", {})
        author_hits[post.author.handle] = int(author_hits.get(post.author.handle, 0)) + 1
        topic_hits = interest_profile.setdefault("topic_hits", {})
        for topic, value in post.topic_scores.items():
            if value <= 0:
                continue
            topic_hits[topic] = round(float(topic_hits.get(topic, 0.0)) + float(value), 4)
        tag_hits = interest_profile.setdefault("tag_hits", {})
        for tag in post.tags:
            tag_hits[tag] = int(tag_hits.get(tag, 0)) + 1
        keyword_hits = interest_profile.setdefault("keyword_hits", {})
        for keyword, value in extract_keywords_for_history(post.primary_text).items():
            if value <= 0:
                continue
            keyword_hits[keyword] = int(keyword_hits.get(keyword, 0)) + int(value)
    history.setdefault("runs", []).append(
        {
            "generated_at": generated_at.isoformat(),
            "selected_handles": [post.author.handle for post in selected_posts],
            "selected_post_ids": [post.id for post in selected_posts],
        }
    )
    history["runs"] = history["runs"][-30:]
    history["authors"] = dict(sorted(authors.items()))
    history["interest_profile"] = {
        "topic_hits": dict(sorted(interest_profile.get("topic_hits", {}).items(), key=lambda item: item[1], reverse=True)[:30]),
        "tag_hits": dict(sorted(interest_profile.get("tag_hits", {}).items(), key=lambda item: item[1], reverse=True)[:30]),
        "keyword_hits": dict(sorted(interest_profile.get("keyword_hits", {}).items(), key=lambda item: item[1], reverse=True)[:80]),
        "author_hits": dict(sorted(interest_profile.get("author_hits", {}).items(), key=lambda item: item[1], reverse=True)[:40]),
    }
    return history


def build_core_pool(history: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    scoring = config["core_authors"]["scoring"]
    authors = []
    for handle, stats in history.get("authors", {}).items():
        score = (
            float(scoring["selected_runs"]) * float(stats.get("selected_runs", 0))
            + float(scoring["avg_priority"]) * float(stats.get("avg_priority", 0.0))
            + float(scoring["avg_topic_relevance"]) * float(stats.get("avg_topic_relevance", 0.0))
            + float(scoring["avg_signal"]) * float(stats.get("avg_signal", 0.0))
        )
        authors.append(
            {
                "handle": handle,
                "score": score,
                "selected_runs": stats.get("selected_runs", 0),
                "avg_priority": stats.get("avg_priority", 0.0),
            }
        )
    authors.sort(key=lambda item: item["score"], reverse=True)
    limit = int(config["core_authors"].get("mode_default_limit", 30))
    return authors[:limit]

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Post, Report
from .personalization import topic_labels


def fallback_enrich(posts: list[Post]) -> None:
    for post in posts:
        if post.summary_bullets:
            continue
        text = post.primary_text.strip()
        extracted = _extract_summary_points(text)
        bullets: list[str] = []
        if extracted:
            bullets.extend(extracted[:3])
        signal_bullet = _signal_bullet(post)
        if signal_bullet:
            bullets.append(signal_bullet)
        post.summary_bullets = _dedupe(bullets)[:4]
        if not post.summary_bullets:
            post.summary_bullets = ["信息密度一般，建议仅在相关主题需要补充案例时快速浏览。"]
        post.why_it_matters = post.why_it_matters or _why_it_matters(post)


def to_markdown(report: Report) -> str:
    stats = report.section_stats or {}
    following_status = report.metadata.get("following_status", {})
    lines = [
        f"# X 晨报 {report.generated_at.strftime('%Y-%m-%d')}",
        "",
        f"- 时间窗口：{report.window_start.strftime('%Y-%m-%d %H:%M')} -> {report.window_end.strftime('%Y-%m-%d %H:%M')}",
        f"- 模式：`{report.mode}`",
        f"- 候选帖子数：{report.metadata.get('candidate_count', 0)}",
        f"- 扫描作者数：{report.metadata.get('author_count', 0)}",
        f"- following 同步：`{following_status.get('synced_count', 0)}`"
        + (
            f" / `{following_status.get('expected_following_count')}`"
            if following_status.get("expected_following_count")
            else ""
        ),
        "",
        "## 今日概览",
        "",
    ]
    if report.overview_bullets:
        for bullet in report.overview_bullets:
            lines.append(f"- {bullet}")
    else:
        lines.append("- 暂无概览。")
    lines.extend(
        [
            "",
            "## 统计摘要",
            "",
            f"- 高匹配帖子：{stats.get('high_fit_posts', 0)} 条",
            f"- 强信号帖子：{stats.get('high_signal_posts', 0)} 条",
            f"- 高价值作者：{', '.join(f'@{handle}' for handle in stats.get('top_authors', [])[:4]) or '暂无'}",
            f"- 高频主题：{'、'.join(stats.get('top_topics', [])) or '暂无'}",
            "",
            "## 今日必读",
            "",
        ]
    )
    must_read = report.must_read
    if must_read:
        lines.extend(render_post_block(must_read))
    else:
        lines.append("- 没有生成今日必读。")
    lines.extend(["", "## Top 10", ""])
    for idx, post in enumerate(report.top_posts, start=1):
        lines.append(f"### #{idx} @{post.author.handle}")
        lines.extend(render_post_block(post))
        lines.append("")
    lines.extend(["## 建议额外关注", ""])
    if report.watchlist_authors:
        for item in report.watchlist_authors:
            handle = item.get("handle", "")
            lines.append(f"- @{handle}: {item.get('reason', '')}")
            for source in item.get("source_posts", []):
                lines.append(f"  来源：{source}")
    else:
        lines.append("- 暂无。")
    return "\n".join(lines).strip() + "\n"


def render_post_block(post: Post) -> list[str]:
    lines = [
        f"- 优先级：`{post.priority_label or 'C'}`",
        f"- 优先级分：`{post.scores.get('priority', 0):.2f}`",
        f"- 原帖链接：{post.url}",
        f"- 为什么值得看：{post.why_it_matters}",
    ]
    if post.tags:
        lines.append(f"- 标签：{', '.join(_localize_tags(post.tags))}")
    lines.append("- 摘要：")
    for bullet in post.summary_bullets[:3]:
        lines.append(f"  - {bullet}")
    return lines


def to_json_payload(report: Report) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at.isoformat(),
        "window": {
            "start": report.window_start.isoformat(),
            "end": report.window_end.isoformat(),
        },
        "mode": report.mode,
        "overview_bullets": report.overview_bullets,
        "section_stats": report.section_stats,
        "metadata": report.metadata,
        "must_read": post_to_dict(report.must_read) if report.must_read else None,
        "top_posts": [post_to_dict(post) for post in report.top_posts],
        "watchlist_authors": report.watchlist_authors,
    }


def post_to_dict(post: Post) -> dict[str, Any]:
    return {
        "id": post.id,
        "conversation_id": post.conversation_id,
        "created_at": post.created_at.isoformat(),
        "text": post.text,
        "url": post.url,
        "author": {
            "handle": post.author.handle,
            "name": post.author.name,
        },
        "signals": {
            "likes": post.like_count,
            "retweets": post.retweet_count,
            "quotes": post.quote_count,
            "replies": post.reply_count,
            "views": post.view_count,
            "bookmarks": post.bookmark_count,
        },
        "scores": post.scores,
        "priority_label": post.priority_label,
        "topic_scores": post.topic_scores,
        "summary_bullets": post.summary_bullets,
        "why_it_matters": post.why_it_matters,
        "tags": post.tags,
    }


def write_outputs(report: Report, config: dict[str, Any]) -> tuple[Path | None, Path | None]:
    local_cfg = config["outputs"]["local"]
    if not local_cfg.get("enabled", True):
        return None, None
    out_dir = Path(local_cfg["directory"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.strftime("%Y-%m-%d")
    md_path = out_dir / f"daily-brief-{stamp}.md"
    json_path = out_dir / f"daily-brief-{stamp}.json"
    md_path.write_text(to_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(to_json_payload(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def _extract_summary_points(text: str) -> list[str]:
    lines = [line.strip(" -•\t") for line in text.splitlines() if line.strip()]
    points: list[str] = []

    for line in lines:
        if re.match(r"^\d+[.)、]", line):
            cleaned = re.sub(r"^\d+[.)、]\s*", "", line).strip()
            if len(cleaned) >= 12:
                points.append(cleaned[:100])

    if len(points) >= 2:
        return points[:3]

    sentence_candidates = re.split(r"[。\n！？]+", text.replace("•", "\n"))
    for sentence in sentence_candidates:
        cleaned = re.sub(r"\s+", " ", sentence).strip(" -")
        if len(cleaned) < 18:
            continue
        if re.search(r"https?://", cleaned):
            cleaned = re.sub(r"https?://\S+", "", cleaned).strip()
        points.append(cleaned[:110])

    return _dedupe(points)[:3]


def _signal_bullet(post: Post) -> str | None:
    if post.bookmark_count >= 40 or post.like_count >= 100:
        return f"社交信号较强：{post.like_count} 赞 / {post.retweet_count} 转推 / {post.bookmark_count} 收藏"
    if post.like_count >= 30 or post.retweet_count >= 10:
        return f"已有一定验证：{post.like_count} 赞 / {post.retweet_count} 转推"
    return None


def _why_it_matters(post: Post) -> str:
    text = post.primary_text.lower()
    if any(keyword in text for keyword in ("完整指南", "教程", "guide", "workflow", "步骤")):
        return "这条更像可直接复用的方法文档，适合快速吸收并迁移到你的工作流。"
    if any(keyword in text for keyword in ("模型", "gemini", "gpt", "claude", "release", "benchmark")):
        return "这条提供了模型或平台层面的新信号，适合用来更新你对工具栈的判断。"
    if any(keyword in text for keyword in ("memory", "记忆", "plugin", "skill", "repo", "github")):
        return "这条有明确的工具或组件增量，适合纳入后续的 skill 与能力池。"
    return "这条和你的长期关注主题匹配，且包含可提炼的实操信息。"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        cleaned = re.sub(r"\s+", " ", item).strip(" -")
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(cleaned)
    return results


def _localize_tags(tags: list[str]) -> list[str]:
    mapping = {
        "ai_coding": "AI 编程",
        "agent_frameworks": "Agent",
        "model_releases": "模型发布",
        "papers_algorithms": "论文/算法",
        "workflow": "工作流",
        "ecosystem": "生态",
        "memory": "记忆",
        "open_source": "开源",
        "data_tools": "数据工具",
        "evaluation": "评测",
        "freshness:high": "新鲜度高",
        "freshness:medium": "新鲜度中",
        "freshness:low": "新鲜度低",
        "fit:high": "高度匹配",
        "fit:medium": "中度匹配",
        "fit:low": "低匹配",
        "author:trusted": "高价值作者",
        "format:thread": "线程/长帖",
        "format:reply": "高质量回复",
        "format:quote": "评论转发",
        "format:link": "带外链",
        "signal:high": "信号强",
        "signal:medium": "信号中",
        "signal:low": "信号弱",
    }
    return [mapping.get(tag, tag) for tag in tags]


def build_report_overview(
    top_posts: list[Post],
    interest_profile: dict[str, Any],
    llm_overview: list[str],
    following_status: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    if llm_overview:
        overview = llm_overview[:4]
    else:
        overview = _fallback_overview(top_posts, interest_profile, following_status)

    tag_counter: Counter[str] = Counter()
    author_counter: Counter[str] = Counter()
    for post in top_posts:
        tag_counter.update(tag for tag in post.tags if not tag.startswith(("freshness:", "signal:", "fit:", "format:")))
        author_counter[post.author.handle] += 1
    stats = {
        "high_fit_posts": sum(1 for post in top_posts if float(post.scores.get("personal_fit", 0.0)) >= 3.0),
        "high_signal_posts": sum(1 for post in top_posts if "signal:high" in post.tags),
        "top_authors": [handle for handle, _ in author_counter.most_common(5)],
        "top_topics": topic_labels([tag for tag, _ in tag_counter.most_common(4)]),
    }
    return overview, stats


def _fallback_overview(top_posts: list[Post], interest_profile: dict[str, Any], following_status: dict[str, Any]) -> list[str]:
    bullets: list[str] = []
    topic_counter: Counter[str] = Counter()
    fit_count = 0
    for post in top_posts:
        topic_counter.update(tag for tag in post.tags if tag in {"ai_coding", "agent_frameworks", "model_releases", "papers_algorithms"})
        if float(post.scores.get("personal_fit", 0.0)) >= 3.0:
            fit_count += 1

    if topic_counter:
        bullets.append(f"今天最值得看的主线集中在：{'、'.join(topic_labels([tag for tag, _ in topic_counter.most_common(3)]))}。")
    if interest_profile.get("summary_bullets"):
        bullets.append(interest_profile["summary_bullets"][0])
    if fit_count:
        bullets.append(f"Top 帖子里有 {fit_count} 条与当前兴趣画像高度匹配，推荐优先看完。")
    if following_status.get("reason"):
        bullets.append(f"following 同步状态：{following_status['reason']}")
    return bullets[:4]

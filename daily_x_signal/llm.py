from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from .models import Post


class LLMClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def is_enabled(self) -> bool:
        if not self.config["llm"].get("enabled", True):
            return False
        return bool(self._api_key())

    def summarize_posts(self, posts: list[Post]) -> dict[str, Any] | None:
        if not posts or not self.is_enabled():
            return None
        api_key = self._api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        base_url = str(self.config["llm"]["base_url"]).rstrip("/")
        timeout = int(self.config["llm"].get("request_timeout_sec", 120))
        model = self.config["llm"]["model"]
        temperature = float(self.config["llm"].get("temperature", 0.2))
        prompt = build_prompt(posts)

        for api_style in _style_order(self.config["llm"].get("api_style", "responses")):
            try:
                if api_style == "responses":
                    response = requests.post(
                        f"{base_url}/responses",
                        headers=headers,
                        timeout=timeout,
                        json={
                            "model": model,
                            "temperature": temperature,
                            "input": prompt,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    text = _extract_responses_text(payload)
                else:
                    response = requests.post(
                        f"{base_url}/chat/completions",
                        headers=headers,
                        timeout=timeout,
                        json={
                            "model": model,
                            "temperature": temperature,
                            "messages": [
                                {"role": "system", "content": "你是一个严格输出 JSON 的中文 X 日报编辑。只输出合法 JSON，不要输出代码块。"},
                                {"role": "user", "content": prompt},
                            ],
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    text = payload["choices"][0]["message"]["content"]
                return json.loads(_clean_json_text(text))
            except Exception:
                continue
        return None

    def _api_key(self) -> str | None:
        direct_key = str(self.config["llm"].get("api_key", "") or "").strip()
        if direct_key:
            return direct_key
        env_name = str(self.config["llm"].get("api_key_env", "") or "").strip()
        if env_name:
            value = os.getenv(env_name)
            if value:
                return value
        return None


def _style_order(preferred: str) -> list[str]:
    if preferred == "chat_completions":
        return ["chat_completions", "responses"]
    return ["responses", "chat_completions"]


def _extract_responses_text(payload: dict[str, Any]) -> str:
    output = payload.get("output", [])
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                return content.get("text", "")
    raise ValueError("No text content in responses payload")


def build_prompt(posts: list[Post]) -> str:
    lines = [
        "你在给一位关注 AI coding、Agent 框架、模型发布、重要论文 的用户生成中文 X 日报。",
        "只返回 JSON，不要 markdown，不要代码块。",
        "输出必须是中文，风格精炼，像真正的投研/情报摘要，不要复述原文。",
        "Schema:",
        '{"posts":[{"id":"tweet id","why_it_matters":"一句中文判断","bullets":["2到4条中文要点"],"tags":["不超过3个中文标签"],"freshness":"high|medium|low","signal":"high|medium|low"}],"must_read_id":"tweet id","watchlist":[{"handle":"不带@","reason":"一句中文说明为什么值得关注"}]}',
        "筛选与摘要规则：",
        "- 优先选择有方法论、架构设计、代码、repo、benchmark、工作流、真实经验、论文洞察的帖子。",
        "- 降权纯情绪、纯站队、纯转述、纯营销、没有新增信息的帖子。",
        "- 必须从给定帖子里选 exactly one must_read_id。",
        "- why_it_matters 要回答“为什么这条值得你看”，不能空泛。",
        "- bullets 要提炼真正的信息增量，不要照抄互动数据，不要堆原句。",
        "- 如果是合集帖，要指出它的价值在于“索引/生态扫描”，而不是假装它是原始研究。",
        "- watchlist 只推荐真正值得后续重点关注的人，理由要具体。",
        "",
        "候选帖子：",
    ]
    for post in posts:
        lines.extend(
            [
                f"ID: {post.id}",
                f"作者: @{post.author.handle}",
                f"链接: {post.url}",
                f"优先级分: {post.scores.get('priority', 0):.2f}",
                f"主题分: {post.scores.get('topic_relevance', 0):.2f} | 干货分: {post.scores.get('substance', 0):.2f} | 社交分: {post.scores.get('social_signal', 0):.2f}",
                f"当前标签: {', '.join(post.tags)}",
                f"互动: likes={post.like_count}, reposts={post.retweet_count}, quotes={post.quote_count}, replies={post.reply_count}, bookmarks={post.bookmark_count}",
                f"正文:\n{post.primary_text}",
                "---",
            ]
        )
    return "\n".join(lines)


def _clean_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def apply_llm_summary(posts: list[Post], llm_payload: dict[str, Any] | None) -> str | None:
    if not llm_payload:
        return None
    posts_by_id = {post.id: post for post in posts}
    for item in llm_payload.get("posts", []):
        post = posts_by_id.get(str(item.get("id")))
        if not post:
            continue
        post.why_it_matters = item.get("why_it_matters", "")[:280]
        post.summary_bullets = [str(bullet).strip() for bullet in item.get("bullets", []) if str(bullet).strip()][:4]
        llm_tags = [str(tag).strip() for tag in item.get("tags", []) if str(tag).strip()]
        if llm_tags:
            post.tags = llm_tags[:3]
        freshness = str(item.get("freshness", "")).strip()
        signal = str(item.get("signal", "")).strip()
        if freshness and freshness not in post.tags:
            post.tags.append(f"freshness:{freshness}")
        if signal and signal not in post.tags:
            post.tags.append(f"signal:{signal}")
    return str(llm_payload.get("must_read_id")) if llm_payload.get("must_read_id") else None


def extract_llm_watchlist(llm_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not llm_payload:
        return []
    results: list[dict[str, Any]] = []
    for item in llm_payload.get("watchlist", []):
        handle = str(item.get("handle", "")).strip().lstrip("@")
        reason = str(item.get("reason", "")).strip()
        if not handle or not reason:
            continue
        results.append({"handle": handle, "reason": reason, "source_posts": []})
    return results

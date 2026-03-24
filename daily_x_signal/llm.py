"""LLM 摘要模块：调用 OpenAI 兼容接口生成中文日报摘要。

支持 Responses API 和 Chat Completions API 双模式自动切换，
带指数退避重试、当日结果缓存和错误回传。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .log import logger
from .models import Post
from .store import load_json, save_json

# 缓存文件路径
_CACHE_PATH = Path(__file__).resolve().parents[1] / "state" / "llm_cache.json"
# 缓存保留天数
_CACHE_RETAIN_DAYS = 3
# 每个 API 风格的最大重试次数
_MAX_RETRIES = 2
# 重试基础等待秒数（指数退避：base * 2^attempt）
_RETRY_BASE_SEC = 2


class LLMClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def is_enabled(self) -> bool:
        if not self.config["llm"].get("enabled", True):
            return False
        return bool(self._api_key())

    def summarize_posts(
        self, posts: list[Post], interest_profile: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """调用 LLM 生成摘要。

        返回 (摘要结果, 错误描述)。成功时错误描述为 None；失败时摘要为 None。
        """
        if not posts or not self.is_enabled():
            return None, None

        # 检查缓存
        cache_key = _build_cache_key(posts, interest_profile)
        cached = _load_cache(cache_key)
        if cached is not None:
            logger.info("LLM 摘要命中缓存，跳过 API 调用")
            return cached, None

        api_key = self._api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        base_url = str(self.config["llm"]["base_url"]).rstrip("/")
        timeout = int(self.config["llm"].get("request_timeout_sec", 120))
        model = self.config["llm"]["model"]
        temperature = float(self.config["llm"].get("temperature", 0.2))
        prompt = build_prompt(posts, interest_profile or {})

        last_error: str = ""
        for api_style in _style_order(self.config["llm"].get("api_style", "responses")):
            logger.info("调用 LLM（api_style=%s, model=%s, 帖子数=%d）", api_style, model, len(posts))
            result, error = self._call_with_retry(
                api_style, base_url, headers, model, temperature, prompt, timeout
            )
            if result is not None:
                logger.info("LLM 摘要成功，处理了 %d 条帖子", len(posts))
                _save_cache(cache_key, result)
                return result, None
            last_error = error or f"{api_style} 调用失败"
            logger.warning("LLM %s 风格失败: %s，尝试下一种", api_style, last_error)

        logger.warning("LLM 摘要最终失败: %s", last_error)
        return None, last_error

    def _call_with_retry(
        self,
        api_style: str,
        base_url: str,
        headers: dict[str, str],
        model: str,
        temperature: float,
        prompt: str,
        timeout: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """单个 API 风格的重试逻辑。只对网络错误和 5xx 重试，4xx 直接放弃。"""
        for attempt in range(_MAX_RETRIES + 1):
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

                # 4xx 错误不重试（认证失败、限流等）
                if 400 <= response.status_code < 500:
                    return None, f"HTTP {response.status_code}: {response.text[:200]}"

                response.raise_for_status()

                if api_style == "responses":
                    text = _extract_responses_text(response.json())
                else:
                    text = response.json()["choices"][0]["message"]["content"]

                return json.loads(_clean_json_text(text)), None

            except (requests.ConnectionError, requests.Timeout) as exc:
                error_msg = f"网络错误: {type(exc).__name__}"
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_BASE_SEC * (2 ** attempt)
                    logger.info("LLM 重试 %d/%d，等待 %ds: %s", attempt + 1, _MAX_RETRIES, wait, error_msg)
                    time.sleep(wait)
                else:
                    return None, error_msg

            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                error_msg = f"HTTP {status}"
                # 5xx 可重试
                if status >= 500 and attempt < _MAX_RETRIES:
                    wait = _RETRY_BASE_SEC * (2 ** attempt)
                    logger.info("LLM 重试 %d/%d，等待 %ds: %s", attempt + 1, _MAX_RETRIES, wait, error_msg)
                    time.sleep(wait)
                else:
                    return None, error_msg

            except json.JSONDecodeError as exc:
                return None, f"JSON 解析失败: {exc}"

            except (ValueError, KeyError) as exc:
                return None, f"响应结构异常: {exc}"

        return None, "重试次数用尽"

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


# ─── 缓存 ───────────────────────────────────────────────

def _build_cache_key(posts: list[Post], interest_profile: dict[str, Any] | None) -> str:
    """基于帖子 ID 列表和兴趣画像生成缓存 key。"""
    post_ids = sorted(p.id for p in posts)
    profile_str = json.dumps(interest_profile or {}, sort_keys=True, ensure_ascii=False)
    raw = json.dumps({"posts": post_ids, "profile": profile_str}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_cache(key: str) -> dict[str, Any] | None:
    cache = load_json(_CACHE_PATH, {})
    entry = cache.get(key)
    if not entry:
        return None
    # 检查是否过期
    cached_at = entry.get("cached_at", "")
    try:
        dt = datetime.fromisoformat(cached_at)
        if datetime.now().astimezone() - dt > timedelta(days=_CACHE_RETAIN_DAYS):
            return None
    except (ValueError, TypeError):
        return None
    return entry.get("data")


def _save_cache(key: str, data: dict[str, Any]) -> None:
    cache = load_json(_CACHE_PATH, {})
    # 清理过期条目
    cutoff = (datetime.now().astimezone() - timedelta(days=_CACHE_RETAIN_DAYS)).isoformat()
    cache = {
        k: v for k, v in cache.items()
        if isinstance(v, dict) and v.get("cached_at", "") > cutoff
    }
    cache[key] = {
        "cached_at": datetime.now().astimezone().isoformat(),
        "data": data,
    }
    save_json(_CACHE_PATH, cache)


# ─── Prompt ──────────────────────────────────────────────

def build_prompt(posts: list[Post], interest_profile: dict[str, Any]) -> str:
    interest_topics = "、".join(interest_profile.get("top_topics", [])[:4]) or "未显式配置"
    interest_keywords = "、".join(interest_profile.get("keywords", [])[:8]) or "未提取到明显关键词"
    lines = [
        "你在给一位高度个性化的 X 用户生成中文日报。",
        "只返回 JSON，不要 markdown，不要代码块。",
        "输出必须是中文，风格精炼，像真正的投研/情报摘要，不要复述原文。",
        "Schema:",
        '{"overview":["2到4条中文总览"],"posts":[{"id":"tweet id","why_it_matters":"一句中文判断","bullets":["2到4条中文要点"],"tags":["不超过3个中文标签"],"freshness":"high|medium|low","signal":"high|medium|low"}],"must_read_id":"tweet id","watchlist":[{"handle":"不带@","reason":"一句中文说明为什么值得关注"}]}',
        "筛选与摘要规则：",
        f"- 用户当前兴趣主题：{interest_topics}",
        f"- 用户当前兴趣关键词：{interest_keywords}",
        "- 优先选择有方法论、架构设计、代码、repo、benchmark、工作流、真实经验、论文洞察的帖子。",
        "- 降权纯情绪、纯站队、纯转述、纯营销、没有新增信息的帖子。",
        "- 必须从给定帖子里选 exactly one must_read_id。",
        "- overview 要先归纳今天真正值得看的总趋势，不要只列帖子标题。",
        "- why_it_matters 要回答「为什么这条值得你看」，不能空泛。",
        "- bullets 要提炼真正的信息增量，不要照抄互动数据，不要堆原句。",
        "- 如果是合集帖，要指出它的价值在于「索引/生态扫描」，而不是假装它是原始研究。",
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
                f"主题分: {post.scores.get('topic_relevance', 0):.2f} | 干货分: {post.scores.get('substance', 0):.2f} | 社交分: {post.scores.get('social_signal', 0):.2f} | 偏好匹配分: {post.scores.get('personal_fit', 0):.2f}",
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


def extract_llm_overview(llm_payload: dict[str, Any] | None) -> list[str]:
    if not llm_payload:
        return []
    overview = []
    for item in llm_payload.get("overview", []):
        text = str(item).strip()
        if text:
            overview.append(text[:120])
    return overview[:4]

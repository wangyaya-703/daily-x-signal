from __future__ import annotations

import math
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .models import Author, Post
from .window import TimeWindow
from .x_client import XReachClient, XReachError


HANDLE_RE = re.compile(r"@([A-Za-z0-9_]{1,15})")


def parse_created_at(value: str) -> datetime:
    return parsedate_to_datetime(value)


def author_from_item(item: dict[str, Any]) -> Author:
    return Author(
        handle=item.get("screenName", ""),
        name=item.get("name", ""),
        followers_count=int(item.get("followersCount", 0) or 0),
        following_count=int(item.get("followingCount", 0) or 0),
        tweet_count=int(item.get("tweetCount", 0) or 0),
        listed_count=int(item.get("listedCount", 0) or 0),
        description=item.get("description", "") or "",
        raw=item,
    )


def post_from_item(item: dict[str, Any]) -> Post:
    user = item.get("user", {})
    author = Author(
        handle=user.get("screenName", ""),
        name=user.get("name", ""),
        raw=user,
    )
    tweet_id = str(item.get("id"))
    handle = author.handle or "unknown"
    return Post(
        id=tweet_id,
        conversation_id=str(item.get("conversationId", tweet_id)),
        created_at=parse_created_at(item["createdAt"]),
        text=(item.get("text") or "").strip(),
        url=f"https://x.com/{handle}/status/{tweet_id}",
        author=author,
        reply_count=int(item.get("replyCount", 0) or 0),
        retweet_count=int(item.get("retweetCount", 0) or 0),
        like_count=int(item.get("likeCount", 0) or 0),
        quote_count=int(item.get("quoteCount", 0) or 0),
        view_count=int(item.get("viewCount", 0) or 0),
        bookmark_count=int(item.get("bookmarkCount", 0) or 0),
        is_reply=bool(item.get("isReply", False)),
        is_quote=bool(item.get("isQuote", False)),
        is_retweet=bool(item.get("isRetweet", False)),
        in_reply_to_tweet_id=item.get("inReplyToTweetId"),
        lang=item.get("lang"),
        raw=item,
    )


def within_window(post: Post, window: TimeWindow) -> bool:
    created = post.created_at.astimezone(window.start.tzinfo)
    return window.start <= created <= window.end


def extract_referenced_handles(text: str) -> list[str]:
    return sorted({match.group(1) for match in HANDLE_RE.finditer(text)})


def collect_authors(client: XReachClient, config: dict[str, Any]) -> list[Author]:
    handle = str(config["x"].get("viewer_handle", "") or "").strip()
    user_id = str(config["x"].get("viewer_user_id", "") or "").strip()
    max_pages = int(config["x"].get("following_sync_max_pages", 1))
    max_authors = int(config["x"].get("max_authors_per_run", 40))
    if not user_id and not handle:
        return []
    try:
        if user_id:
            payload = client.following_by_user_id(user_id, max_pages=max_pages, count=50)
        else:
            payload = client.following(handle, max_pages=max_pages, count=50)
        items = payload.get("items", [])
    except XReachError:
        return []
    authors = [author_from_item(item) for item in items[:max_authors]]
    return authors


def authors_from_cache(payload: dict[str, Any], limit: int) -> list[Author]:
    items = payload.get("authors", [])
    return [author_from_item(item) for item in items[:limit]]


def collect_home_candidates(client: XReachClient, window: TimeWindow) -> list[Post]:
    payload = client.home()
    posts = [post_from_item(item) for item in payload.get("items", [])]
    return [post for post in posts if within_window(post, window)]


def prioritize_authors(authors: list[Author], home_posts: list[Post], limit: int) -> list[Author]:
    if not authors:
        return []
    home_rank: dict[str, float] = {}
    for post in home_posts:
        handle = post.author.handle
        home_rank.setdefault(handle, 0.0)
        home_rank[handle] += (
            post.like_count
            + 2 * post.retweet_count
            + 2 * post.quote_count
            + 1.5 * post.bookmark_count
        )
    author_index = {author.handle: author for author in authors}
    prioritized: list[Author] = []
    for handle, _score in sorted(home_rank.items(), key=lambda item: item[1], reverse=True):
        author = author_index.get(handle)
        if author:
            prioritized.append(author)
    for author in sorted(authors, key=lambda item: item.followers_count, reverse=True):
        if author.handle not in {a.handle for a in prioritized}:
            prioritized.append(author)
    return prioritized[:limit]


def collect_posts_for_authors(
    client: XReachClient,
    authors: list[Author],
    config: dict[str, Any],
    window: TimeWindow,
) -> list[Post]:
    posts: list[Post] = []
    max_pages = int(config["x"].get("tweets_pages_per_author", 1))
    include_replies = bool(config["x"].get("include_replies", True))
    min_len = int(config["x"].get("min_post_length", 0))
    reply_threshold = int(config["x"].get("reply_like_threshold", 0))
    for author in authors:
        if not author.handle:
            continue
        try:
            payload = client.tweets(author.handle, replies=include_replies, max_pages=max_pages, count=40)
        except XReachError:
            continue
        for item in payload.get("items", []):
            post = post_from_item(item)
            if post.is_reply and post.like_count < reply_threshold:
                continue
            if len(post.text.strip()) < min_len and "http" not in post.text:
                continue
            if within_window(post, window):
                posts.append(post)
    return posts


def hydrate_threads(client: XReachClient, posts: list[Post], top_n: int) -> None:
    for post in posts[:top_n]:
        try:
            thread_items = client.thread(post.id)
        except XReachError:
            continue
        if not isinstance(thread_items, list):
            continue
        thread_posts = [post_from_item(item) for item in thread_items if item.get("conversationId") == post.conversation_id]
        if thread_posts:
            post.thread_posts = thread_posts


def dedupe_posts(posts: list[Post], dedupe_by_conversation: bool = True) -> list[Post]:
    seen: set[str] = set()
    deduped: list[Post] = []
    for post in sorted(posts, key=lambda p: p.created_at, reverse=True):
        key = post.conversation_id if dedupe_by_conversation else post.id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(post)
    return deduped


def limit_posts_per_author(posts: list[Post], max_posts_per_author: int) -> list[Post]:
    if max_posts_per_author <= 0:
        return posts
    counts: dict[str, int] = {}
    limited: list[Post] = []
    for post in posts:
        handle = post.author.handle
        counts.setdefault(handle, 0)
        if counts[handle] >= max_posts_per_author:
            continue
        counts[handle] += 1
        limited.append(post)
    return limited


def build_signal_snapshot(post: Post) -> dict[str, float]:
    return {
        "likes": float(post.like_count),
        "retweets": float(post.retweet_count),
        "quotes": float(post.quote_count),
        "bookmarks": float(post.bookmark_count),
        "views": float(post.view_count),
        "replies": float(post.reply_count),
        "engagement_log": math.log1p(
            post.like_count
            + 2 * post.retweet_count
            + 2 * post.quote_count
            + 1.5 * post.bookmark_count
            + 0.5 * post.reply_count
        ),
    }

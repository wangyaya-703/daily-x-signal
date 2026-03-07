from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Author:
    handle: str
    name: str = ""
    followers_count: int = 0
    following_count: int = 0
    tweet_count: int = 0
    listed_count: int = 0
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Post:
    id: str
    conversation_id: str
    created_at: datetime
    text: str
    url: str
    author: Author
    reply_count: int = 0
    retweet_count: int = 0
    like_count: int = 0
    quote_count: int = 0
    view_count: int = 0
    bookmark_count: int = 0
    is_reply: bool = False
    is_quote: bool = False
    is_retweet: bool = False
    in_reply_to_tweet_id: str | None = None
    lang: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    thread_posts: list["Post"] = field(default_factory=list)
    topic_scores: dict[str, float] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    summary_bullets: list[str] = field(default_factory=list)
    why_it_matters: str = ""
    tags: list[str] = field(default_factory=list)
    priority_label: str = ""

    @property
    def primary_text(self) -> str:
        if self.thread_posts:
            return "\n".join(p.text for p in self.thread_posts if p.text)
        return self.text


@dataclass(slots=True)
class Report:
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    mode: str
    top_posts: list[Post]
    must_read: Post | None
    watchlist_authors: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)

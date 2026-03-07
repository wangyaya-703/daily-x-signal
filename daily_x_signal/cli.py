from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .config import AppConfig
from .collector import (
    authors_from_cache,
    collect_authors,
    collect_home_candidates,
    collect_posts_for_authors,
    dedupe_posts,
    hydrate_threads,
    limit_posts_per_author,
    prioritize_authors,
)
from .core_authors import author_stats_from_history, build_core_pool, load_history, save_history, update_history
from .feishu import deliver_feishu
from .llm import LLMClient, apply_llm_summary, extract_llm_watchlist
from .models import Report
from .report import fallback_enrich, write_outputs
from .scoring import rank_posts, suggested_authors
from .store import load_json, save_json
from .window import resolve_window
from .x_client import XReachClient


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "default.yaml"
STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "history.json"
FOLLOWING_CACHE_PATH = Path(__file__).resolve().parents[1] / "state" / "following_cache.json"
CORE_POOL_PATH = Path(__file__).resolve().parents[1] / "state" / "core_authors.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-x-signal")
    parser.add_argument("command", choices=["generate", "sync-authors", "show-core-authors"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--override-config")
    parser.add_argument("--window-mode", choices=["scheduled", "rolling_24h"], default="scheduled")
    parser.add_argument("--mode", choices=["all_following", "core_authors"], default=None)
    parser.add_argument("--top-n", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def cmd_sync_authors(config: dict, client: XReachClient) -> int:
    authors = collect_authors(client, config)
    payload = {
        "refreshed_at": datetime.now().isoformat(),
        "authors": [author.raw for author in authors],
    }
    save_json(FOLLOWING_CACHE_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_show_core_authors(config: dict) -> int:
    history = load_history(STATE_PATH)
    pool = build_core_pool(history, config)
    save_json(CORE_POOL_PATH, {"generated_at": datetime.now().isoformat(), "authors": pool})
    print(json.dumps(pool, ensure_ascii=False, indent=2))
    return 0


def cmd_generate(args: argparse.Namespace, config: dict, client: XReachClient) -> int:
    history = load_history(STATE_PATH)
    author_stats = author_stats_from_history(history)
    following_cache = load_json(FOLLOWING_CACHE_PATH, {"authors": []})
    timezone_name = config["profile"]["timezone"]
    window = resolve_window(args.window_mode, timezone_name)
    mode = args.mode or config["profile"].get("default_mode", "all_following")

    home_candidates = []
    if config["x"].get("fallback_to_home_timeline", True):
        try:
            home_candidates = collect_home_candidates(client, window)
        except Exception:
            home_candidates = []

    authors = collect_authors(client, config)
    following_cache_used = False
    if authors:
        save_json(
            FOLLOWING_CACHE_PATH,
            {"refreshed_at": datetime.now().isoformat(), "authors": [author.raw for author in authors]},
        )
    else:
        authors = authors_from_cache(following_cache, int(config["x"].get("max_authors_per_run", 40)))
        following_cache_used = bool(authors)

    candidate_posts = list(home_candidates)
    if mode == "core_authors":
        pool = build_core_pool(history, config)
        core_handles = {item["handle"] for item in pool}
        authors = [author for author in authors if author.handle in core_handles]
    prioritized_authors = prioritize_authors(
        authors,
        home_candidates,
        int(config["x"].get("max_active_authors_per_run", len(authors) or 0)),
    )
    if prioritized_authors:
        candidate_posts.extend(collect_posts_for_authors(client, prioritized_authors, config, window))
    candidate_posts = dedupe_posts(candidate_posts, bool(config["x"].get("dedupe_by_conversation", True)))
    ranked = rank_posts(candidate_posts, config, author_stats)
    ranked = limit_posts_per_author(ranked, int(config["ranking"].get("max_posts_per_author", 2)))
    top_n = args.top_n or int(config["profile"].get("digest_top_n", 10))
    hydrate_threads(client, ranked, int(config["x"].get("thread_fetch_top_n", 12)))

    llm_client = LLMClient(config)
    llm_payload = llm_client.summarize_posts(ranked[: int(config["llm"].get("max_input_posts", 12))])
    must_read_id = apply_llm_summary(ranked, llm_payload)
    llm_watchlist = extract_llm_watchlist(llm_payload)
    fallback_enrich(ranked[:top_n])

    top_posts = ranked[:top_n]
    must_read = next((post for post in top_posts if post.id == must_read_id), top_posts[0] if top_posts else None)
    watchlist = merge_watchlists(suggested_authors(top_posts, author_stats), llm_watchlist)
    report = Report(
        generated_at=datetime.now(window.end.tzinfo),
        window_start=window.start,
        window_end=window.end,
        mode=mode,
        top_posts=top_posts,
        must_read=must_read,
        watchlist_authors=watchlist,
        metadata={
            "candidate_count": len(candidate_posts),
            "author_count": len(prioritized_authors or authors),
            "following_cache_used": following_cache_used,
            "llm_enabled": llm_client.is_enabled(),
        },
    )

    if args.dry_run:
        print(json.dumps({"top_post_urls": [post.url for post in top_posts], "must_read": must_read.url if must_read else None}, ensure_ascii=False, indent=2))
        return 0

    md_path, json_path = write_outputs(report, config)
    history = update_history(history, top_posts, report.generated_at)
    save_history(STATE_PATH, history)
    core_pool = build_core_pool(history, config)
    save_json(CORE_POOL_PATH, {"generated_at": report.generated_at.isoformat(), "authors": core_pool})
    feishu_preview_path, feishu_status = deliver_feishu(report, config)
    print(f"Markdown: {md_path}")
    print(f"JSON: {json_path}")
    if feishu_preview_path:
        print(f"Feishu preview: {feishu_preview_path}")
    if feishu_status:
        print(f"Feishu status: {feishu_status}")
    print(f"Core author pool size: {len(core_pool)}")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base_config = AppConfig.load(args.config)
    config = base_config.merged_with(args.override_config).raw
    client = XReachClient(workdir=Path.cwd())
    if args.command == "sync-authors":
        return cmd_sync_authors(config, client)
    if args.command == "show-core-authors":
        return cmd_show_core_authors(config)
    return cmd_generate(args, config, client)


def merge_watchlists(primary: list[dict], secondary: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for item in [*secondary, *primary]:
        handle = str(item.get("handle", "")).lstrip("@")
        if not handle or handle in seen:
            continue
        seen.add(handle)
        merged.append(item)
    return merged


if __name__ == "__main__":
    raise SystemExit(main())

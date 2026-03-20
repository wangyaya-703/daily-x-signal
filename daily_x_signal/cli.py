from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .config import AppConfig
from .console import bool_text, print_section, render_table
from .collector import (
    authors_from_cache,
    collect_home_candidates,
    collect_posts_for_authors,
    dedupe_posts,
    hydrate_threads,
    limit_posts_per_author,
    prioritize_authors,
    sync_following,
)
from .core_authors import author_stats_from_history, build_core_pool, load_history, save_history, update_history
from .feishu import deliver_feishu, deliver_feishu_bitable
from .feishu_bitable import bitable_app_url
from .github_fallback import sync_success_marker
from .llm import LLMClient, apply_llm_summary, extract_llm_overview, extract_llm_watchlist
from .models import Report
from .personalization import build_interest_profile
from .report import build_report_overview, fallback_enrich, write_outputs
from .scheduler import load_scheduler_state, record_scheduler_result, should_run_scheduler
from .scoring import rank_posts, suggested_authors
from .setup_wizard import run_setup
from .store import load_json, save_json
from .window import resolve_window
from .x_client import XReachClient


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "default.yaml"
LOCAL_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "local.yaml"
STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "history.json"
FOLLOWING_CACHE_PATH = Path(__file__).resolve().parents[1] / "state" / "following_cache.json"
CORE_POOL_PATH = Path(__file__).resolve().parents[1] / "state" / "core_authors.json"
SCHEDULER_STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "scheduler_state.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-x-signal")
    parser.add_argument("command", choices=["generate", "sync-authors", "show-core-authors", "schedule-tick", "setup"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--override-config")
    parser.add_argument("--window-mode", choices=["scheduled", "rolling_24h"], default="scheduled")
    parser.add_argument("--mode", choices=["all_following", "core_authors"], default=None)
    parser.add_argument("--top-n", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def cmd_sync_authors(config: dict, client: XReachClient) -> int:
    sync_result = sync_following(client, config)
    authors = sync_result["authors"]
    payload = {
        "refreshed_at": datetime.now().isoformat(),
        "status": sync_result["status"],
        "authors": [author.raw for author in authors],
    }
    save_json(FOLLOWING_CACHE_PATH, payload)
    print_section("following 同步结果")
    print(
        render_table(
            ["Field", "Value"],
            [
                ["refreshed_at", payload["refreshed_at"]],
                ["synced_count", sync_result["status"].get("synced_count", 0)],
                ["expected_following_count", sync_result["status"].get("expected_following_count", "")],
                ["completion_ratio", sync_result["status"].get("completion_ratio", "")],
                ["is_complete", bool_text(bool(sync_result["status"].get("is_complete", False)))],
                ["needs_confirmation", bool_text(bool(sync_result["status"].get("needs_confirmation", False)))],
                ["reason", sync_result["status"].get("reason", "")],
            ],
        )
    )
    if authors:
        print_section("作者样本")
        print(
            render_table(
                ["Handle", "Name", "Followers"],
                [[author.handle, author.name, author.followers_count] for author in authors[:10]],
            )
        )
    return 0


def cmd_show_core_authors(config: dict) -> int:
    history = load_history(STATE_PATH)
    pool = build_core_pool(history, config)
    save_json(CORE_POOL_PATH, {"generated_at": datetime.now().isoformat(), "authors": pool})
    print(json.dumps(pool, ensure_ascii=False, indent=2))
    return 0


def generate_digest(args: argparse.Namespace, config: dict, client: XReachClient) -> dict[str, object]:
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

    sync_result = sync_following(client, config)
    authors = sync_result["authors"]
    following_status = sync_result["status"]
    following_cache_used = False
    if authors:
        save_json(
            FOLLOWING_CACHE_PATH,
            {
                "refreshed_at": datetime.now().isoformat(),
                "status": following_status,
                "authors": [author.raw for author in authors],
            },
        )
    else:
        authors = authors_from_cache(following_cache, int(config["x"].get("max_authors_per_run", 0) or 0) or len(following_cache.get("authors", [])))
        following_cache_used = bool(authors)
        following_status = following_cache.get("status", following_status)

    if following_status.get("needs_confirmation") and bool(config["x"].get("require_following_confirmation", True)):
        print(f"[warning] {following_status.get('reason')}")

    candidate_posts = list(home_candidates)
    if mode == "core_authors":
        pool = build_core_pool(history, config)
        core_handles = {item["handle"] for item in pool}
        authors = [author for author in authors if author.handle in core_handles]

    interest_profile = build_interest_profile(config, authors, history)
    prioritized_authors = prioritize_authors(
        authors,
        home_candidates,
        int(config["x"].get("max_active_authors_per_run", len(authors) or 0)),
    )
    if prioritized_authors:
        candidate_posts.extend(collect_posts_for_authors(client, prioritized_authors, config, window))
    candidate_posts = dedupe_posts(candidate_posts, bool(config["x"].get("dedupe_by_conversation", True)))
    ranked = rank_posts(candidate_posts, config, author_stats, interest_profile=interest_profile)
    ranked = limit_posts_per_author(ranked, int(config["ranking"].get("max_posts_per_author", 2)))
    top_n = args.top_n or int(config["profile"].get("digest_top_n", 10))
    hydrate_threads(client, ranked, int(config["x"].get("thread_fetch_top_n", 12)))

    llm_client = LLMClient(config)
    llm_payload = llm_client.summarize_posts(
        ranked[: int(config["llm"].get("max_input_posts", 12))],
        interest_profile=interest_profile,
    )
    must_read_id = apply_llm_summary(ranked, llm_payload)
    llm_watchlist = extract_llm_watchlist(llm_payload)
    llm_overview = extract_llm_overview(llm_payload)
    fallback_enrich(ranked[:top_n])

    top_posts = ranked[:top_n]
    must_read = next((post for post in top_posts if post.id == must_read_id), top_posts[0] if top_posts else None)
    watchlist = merge_watchlists(suggested_authors(top_posts, author_stats), llm_watchlist)
    overview_bullets, section_stats = build_report_overview(top_posts, interest_profile, llm_overview, following_status)
    report = Report(
        generated_at=datetime.now(window.end.tzinfo),
        window_start=window.start,
        window_end=window.end,
        mode=mode,
        top_posts=top_posts,
        must_read=must_read,
        watchlist_authors=watchlist,
        overview_bullets=overview_bullets,
        section_stats=section_stats,
        metadata={
            "candidate_count": len(candidate_posts),
            "author_count": len(prioritized_authors or authors),
            "following_cache_used": following_cache_used,
            "following_status": following_status,
            "interest_profile": interest_profile,
            "llm_enabled": llm_client.is_enabled(),
        },
    )

    if args.dry_run:
        return {
            "dry_run": True,
            "top_post_urls": [post.url for post in top_posts],
            "must_read_url": must_read.url if must_read else None,
            "report": report,
        }

    md_path, json_path = write_outputs(report, config)
    history = update_history(history, top_posts, report.generated_at)
    save_history(STATE_PATH, history)
    core_pool = build_core_pool(history, config)
    save_json(CORE_POOL_PATH, {"generated_at": report.generated_at.isoformat(), "authors": core_pool})
    feishu_preview_path, feishu_status = deliver_feishu(report, config)
    bitable_preview_path, bitable_record_id = deliver_feishu_bitable(report, config)
    return {
        "report": report,
        "md_path": md_path,
        "json_path": json_path,
        "feishu_preview_path": feishu_preview_path,
        "feishu_status": feishu_status,
        "bitable_preview_path": bitable_preview_path,
        "bitable_record_id": bitable_record_id,
        "core_pool_size": len(core_pool),
    }


def cmd_generate(args: argparse.Namespace, config: dict, client: XReachClient) -> int:
    result = generate_digest(args, config, client)
    if result.get("dry_run"):
        print(
            json.dumps(
                {
                    "top_post_urls": result.get("top_post_urls", []),
                    "must_read": result.get("must_read_url"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    md_path = result.get("md_path")
    json_path = result.get("json_path")
    feishu_preview_path = result.get("feishu_preview_path")
    feishu_status = result.get("feishu_status")
    bitable_preview_path = result.get("bitable_preview_path")
    bitable_record_id = result.get("bitable_record_id")
    print_section("生成结果")
    print(
        render_table(
            ["Item", "Value"],
            [
                ["Markdown", str(md_path or "")],
                ["JSON", str(json_path or "")],
                ["Core author pool size", result.get("core_pool_size", 0)],
            ],
        )
    )
    print_section("投递状态")
    print(
        render_table(
            ["Channel", "Enabled", "Result", "Detail"],
            [
                [
                    "Feishu Card",
                    bool_text(bool(config.get("outputs", {}).get("feishu", {}).get("enabled", False))),
                    "OK" if feishu_status == 200 else ("SKIP" if feishu_preview_path and not feishu_status else "WARN"),
                    str(feishu_preview_path or feishu_status or "未启用"),
                ],
                [
                    "Feishu Bitable",
                    bool_text(bool(config.get("outputs", {}).get("feishu_bitable", {}).get("enabled", False))),
                    "OK" if bitable_record_id else ("SKIP" if bitable_preview_path else "WARN"),
                    str(bitable_record_id or bitable_preview_path or "未启用"),
                ],
            ],
        )
    )
    if config.get("outputs", {}).get("feishu_bitable", {}).get("enabled", False):
        print_section("帖子追踪表")
        print(
            render_table(
                ["Field", "Value"],
                [
                    ["Open URL", bitable_app_url(config) or ""],
                    ["Write Model", "按 Post ID upsert，1 个帖子 1 行"],
                    ["Tracked Fields", "Priority / Priority Score / Original URL / Summary / Published At / Topics"],
                ],
            )
        )
    return 0


def cmd_schedule_tick(args: argparse.Namespace, config: dict, client: XReachClient) -> int:
    scheduler_state = load_scheduler_state(SCHEDULER_STATE_PATH)
    decision = should_run_scheduler(config, scheduler_state)
    if not decision["should_run"] and not args.force:
        print(decision["reason"])
        return 0

    scheduled_args = argparse.Namespace(
        command="generate",
        config=args.config,
        override_config=args.override_config,
        window_mode="scheduled",
        mode=args.mode,
        top_n=args.top_n,
        dry_run=False,
        force=args.force,
    )
    try:
        result = generate_digest(scheduled_args, config, client)
        report = result["report"]
        feishu_enabled = bool(config["outputs"]["feishu"].get("enabled", False))
        feishu_status = result.get("feishu_status")
        success = bool(result.get("md_path")) and (not feishu_enabled or feishu_status == 200)
        record_scheduler_result(
            SCHEDULER_STATE_PATH,
            digest_date=report.window_end.date().isoformat(),
            reason=str(decision["reason"]),
            success=success,
            feishu_status=feishu_status if isinstance(feishu_status, int) else None,
            metadata={
                "window_end": report.window_end.isoformat(),
                "markdown_path": str(result.get("md_path") or ""),
                "json_path": str(result.get("json_path") or ""),
            },
        )
        if not success:
            raise RuntimeError("日报已生成，但飞书发送未成功。")
        marker_result = sync_success_marker(
            config,
            digest_date=report.window_end.date().isoformat(),
            sent_at=report.generated_at.isoformat(),
        )
        print(f"Scheduled digest sent for {report.window_end.date().isoformat()}")
        print(f"Feishu status: {feishu_status}")
        print(f"GitHub fallback marker: {marker_result['reason']}")
        return 0
    except Exception as exc:
        latest_state = load_scheduler_state(SCHEDULER_STATE_PATH)
        sent_dates = latest_state.get("sent_dates", {})
        if str(decision["digest_date"]) in sent_dates:
            raise
        record_scheduler_result(
            SCHEDULER_STATE_PATH,
            digest_date=str(decision["digest_date"]),
            reason=str(decision["reason"]),
            success=False,
            metadata={"error": str(exc)},
        )
        raise


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "setup":
        target_config = Path(args.override_config) if args.override_config else LOCAL_CONFIG_PATH
        return run_setup(
            base_config_path=Path(args.config),
            target_config_path=target_config,
            client=XReachClient(workdir=Path.cwd()),
            history_path=STATE_PATH,
            following_cache_path=FOLLOWING_CACHE_PATH,
        )
    base_config = AppConfig.load(args.config)
    config = base_config.merged_with(args.override_config).raw
    client = XReachClient(workdir=Path.cwd())
    if args.command == "sync-authors":
        return cmd_sync_authors(config, client)
    if args.command == "show-core-authors":
        return cmd_show_core_authors(config)
    if args.command == "schedule-tick":
        return cmd_schedule_tick(args, config, client)
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

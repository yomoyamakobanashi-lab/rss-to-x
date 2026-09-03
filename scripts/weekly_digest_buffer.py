#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import post_thread

RSS_URL = "https://anchor.fm/s/10422ca68/podcast/rss"
STATE_PATH = ROOT / "state_promo.json"
MAX_RECENT_URLS = 60


def clip_title(title: str, limit: int = 90) -> str:
    title = " ".join((title or "").split())
    return title if len(title) <= limit else title[: limit - 1] + "…"


def timestamp(entry) -> float:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return 0
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0


def load_state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        urls = state.get("recent_urls", [])
        return {
            "recent_urls": [str(url) for url in urls if str(url).strip()],
            "spotlight_variant": max(0, int(state.get("spotlight_variant", 0))),
        }
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return {"recent_urls": [], "spotlight_variant": 0}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(
            {
                "recent_urls": state["recent_urls"][-MAX_RECENT_URLS:],
                "spotlight_variant": int(state.get("spotlight_variant", 0)),
            },
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )


def choose_three(items, recent_urls: list[str]):
    ordered = sorted(items, key=timestamp, reverse=True)
    archive_pool = ordered[3:] if len(ordered) > 6 else ordered
    fresh_pool = [x for x in archive_pool if x.get("link", "").strip() not in recent_urls]

    chosen = []
    for pool in (fresh_pool, archive_pool, ordered):
        remaining = [x for x in pool if x.get("link") not in {c.get("link") for c in chosen}]
        random.shuffle(remaining)
        for item in remaining:
            chosen.append(item)
            if len(chosen) == 3:
                return chosen
    return chosen


def main() -> None:
    feed = feedparser.parse(RSS_URL)
    items = [entry for entry in feed.entries if entry.get("title") and entry.get("link")]
    if not items:
        raise RuntimeError("No RSS items found")

    state = load_state()
    recent_urls = state["recent_urls"]
    episodes = choose_three(items, recent_urls)
    if len(episodes) < 3:
        raise RuntimeError("Not enough RSS items for weekly digest")

    posts = [
        "週末、映画の話をもう少ししたい人へ。\n\n"
        "過去回から3本選びました。①〜③、いま一番気になるのはどれ？"
    ]
    used_urls = []
    for number, item in enumerate(episodes, start=1):
        url = item.get("link", "").strip()
        used_urls.append(url)
        posts.append(f"{number}️⃣ {clip_title(item.get('title', ''))}\n{url}")

    post_id = post_thread(posts)

    for url in used_urls:
        recent_urls = [u for u in recent_urls if u != url] + [url]
    state["recent_urls"] = recent_urls
    save_state(state)
    print(f"[OK] Buffer accepted weekly archive thread: {post_id}; urls={len(used_urls)}")


if __name__ == "__main__":
    main()

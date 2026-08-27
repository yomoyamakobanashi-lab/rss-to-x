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
MAX_RECENT_URLS = 16
ARCHIVE_MIN_AGE_DAYS = 14


def clip_title(title: str, limit: int = 90) -> str:
    title = " ".join((title or "").split())
    return title if len(title) <= limit else title[: limit - 1] + "…"


def age_days(entry) -> float | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        published = datetime(*parsed[:6], tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - published).total_seconds() / 86400
    except Exception:
        return None


def load_recent_urls() -> list[str]:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        urls = state.get("recent_urls", [])
        return [str(url) for url in urls if str(url).strip()]
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return []


def save_recent_urls(urls: list[str]) -> None:
    STATE_PATH.write_text(
        json.dumps({"recent_urls": urls[-MAX_RECENT_URLS:]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    feed = feedparser.parse(RSS_URL)
    items = [entry for entry in feed.entries if (entry.get("title") and entry.get("link"))]
    if not items:
        raise RuntimeError("No RSS items found")

    archive_items = [
        item for item in items
        if age_days(item) is None or age_days(item) >= ARCHIVE_MIN_AGE_DAYS
    ]
    pool = archive_items or items

    recent_urls = load_recent_urls()
    available = [item for item in pool if item.get("link", "").strip() not in recent_urls]
    item = random.choice(available or pool)

    title = clip_title(item.get("title", ""))
    url = item.get("link", "").strip()

    root = (
        f"今夜の一本は『{title}』。\n\n"
        "この作品、あなたは「好き」「苦手」「まだ観てない」のどれ？"
    )
    reply = (
        "🎧 リルパルの過去回はこちら。\n"
        f"{url}\n\n"
        "聴いたことがある人は、異論も歓迎です。"
    )

    post_id = post_thread([root, reply])

    recent_urls = [u for u in recent_urls if u != url] + [url]
    save_recent_urls(recent_urls)
    print(f"[OK] Buffer accepted Friday episode thread: {post_id}; url={url}")


if __name__ == "__main__":
    main()

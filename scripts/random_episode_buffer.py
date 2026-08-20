#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import random
import sys
from pathlib import Path

import feedparser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import post_text

RSS_URL = "https://anchor.fm/s/10422ca68/podcast/rss"
PHRASES_PATH = "data/phrases.txt"


def read_phrases() -> list[str]:
    try:
        with open(PHRASES_PATH, encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
            if lines:
                return lines
    except FileNotFoundError:
        pass
    return [
        "今日の過去回：{title}\n{url}",
        "聴き逃しから一本：{title}\n{url}",
        "週の途中に過去回を一本。{title}\n{url}",
    ]


def clip_title(title: str, limit: int = 90) -> str:
    title = " ".join((title or "").split())
    return title if len(title) <= limit else title[: limit - 1] + "…"


def main() -> None:
    feed = feedparser.parse(RSS_URL)
    items = [entry for entry in feed.entries if (entry.get("title") and entry.get("link"))]
    if not items:
        raise RuntimeError("No RSS items found")

    item = random.choice(items)
    phrase = random.choice(read_phrases())
    text = phrase.replace("{title}", clip_title(item.get("title", ""))).replace(
        "{url}", item.get("link", "").strip()
    )
    post_id = post_text(text)
    print(f"[OK] Buffer accepted random episode post: {post_id}")


if __name__ == "__main__":
    main()

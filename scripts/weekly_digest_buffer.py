#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import random
from datetime import datetime, timezone

import feedparser

from buffer_client import post_thread

RSS_URL = "https://anchor.fm/s/10422ca68/podcast/rss"
HOOKS_PATH = "data/weekly_hooks.txt"


def read_hooks() -> list[str]:
    try:
        with open(HOOKS_PATH, encoding="utf-8") as f:
            hooks = [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
            if hooks:
                return hooks
    except FileNotFoundError:
        pass
    return [
        "週末の過去回セレクト、3本置いていきます。",
        "聴き逃し救済。今週の3本です。",
        "週末用に、過去回を3本まとめます。",
    ]


def timestamp(entry) -> float:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return 0
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0


def choose_three(items):
    ordered = sorted(items, key=timestamp, reverse=True)
    if len(ordered) <= 3:
        return ordered
    newest = ordered[0]
    recent_pool = ordered[1:min(len(ordered), 20)]
    older_pool = ordered[min(len(ordered), 20):] or ordered[1:]
    second = random.choice(recent_pool)
    third_pool = [x for x in older_pool if x.get("link") != second.get("link")]
    third = random.choice(third_pool or ordered[1:])
    return [newest, second, third]


def main() -> None:
    feed = feedparser.parse(RSS_URL)
    items = [entry for entry in feed.entries if entry.get("title") and entry.get("link")]
    if not items:
        raise RuntimeError("No RSS items found")

    episodes = choose_three(items)
    posts = [f"{random.choice(read_hooks())}\n（①〜③で貼ります）\n#リルパル #ReelPal"]
    for number, item in enumerate(episodes, start=1):
        posts.append(f"{number}️⃣ {item.get('title', '').strip()}\n{item.get('link', '').strip()}")

    post_id = post_thread(posts)
    print(f"[OK] Buffer accepted weekly thread: {post_id}")


if __name__ == "__main__":
    main()

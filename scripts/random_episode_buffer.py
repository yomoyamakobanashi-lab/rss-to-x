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
from scripts.episode_links import render_episode_reply

RSS_URL = "https://anchor.fm/s/10422ca68/podcast/rss"
STATE_PATH = ROOT / "state_promo.json"
MAX_RECENT_URLS = 60
ARCHIVE_MIN_AGE_DAYS = 14

ROOT_TEMPLATES = [
    "アーカイブから今日の一本。『{title}』。\n\n観た人、まず一言で言うとどんな映画でした？",
    "いま掘り返したい過去回は『{title}』。\n\n好き、苦手、未見。いまの距離感はどれ？",
    "今日のリルパル発掘枠は『{title}』。\n\nこの作品、誰かと話したくなるタイプでした？",
    "過去回を一つだけ置いていきます。『{title}』。\n\nあなたがこの映画で一番覚えている場面は？",
    "本日のアーカイブ散歩は『{title}』。\n\n初見の印象と、いまの評価は同じですか？",
    "少し前の映画談義から『{title}』を。\n\n人に薦めるなら、どんな一言を添えます？",
    "今日もう一度話したいのは『{title}』。\n\nこの作品、どこで好みが分かれると思う？",
    "リルパルの棚から一回分。『{title}』。\n\nタイトルを見て、最初に浮かんだ感想は？",
    "いま聴き返すなら『{title}』回。\n\n観た当時の自分に一言返せるなら、何と言います？",
    "今日の過去回ルーレットは『{title}』。\n\nこれは一人で観たい映画？ 誰かと観たい映画？",
    "アーカイブに眠らせておくには惜しいので『{title}』。\n\nこの映画の話、どこから始めたい？",
    "本日の一本は『{title}』。\n\n評価より先に、観終わった直後の感情を一語でどうぞ。",
]


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

    state = load_state()
    recent_urls = state["recent_urls"]
    available = [item for item in pool if item.get("link", "").strip() not in recent_urls]
    item = random.choice(available or pool)

    title = clip_title(item.get("title", ""))
    url = item.get("link", "").strip()

    variant = state["spotlight_variant"] % len(ROOT_TEMPLATES)
    root = ROOT_TEMPLATES[variant].format(title=title)
    reply = render_episode_reply(
        title=item.get("title"),
        guid=item.get("id") or item.get("guid"),
        intro="🎧 リルパルの過去回を聴く",
    )

    post_id = post_thread([root, reply])

    state["recent_urls"] = [u for u in recent_urls if u != url] + [url]
    state["spotlight_variant"] += 1
    save_state(state)
    print(
        f"[OK] Buffer accepted archive spotlight thread: {post_id}; "
        f"url={url}; variant={variant}"
    )


if __name__ == "__main__":
    main()

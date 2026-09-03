#!/usr/bin/env python3
"""Create two grounded social-pack items from the newest verified chapter file.

The chapter file is produced from LISTEN.  This module deliberately does not
invent opinions or dialogue: it only turns existing chapter titles into short
route maps for X.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LATEST_PATH = ROOT / "data" / "generated_chapters" / "latest.json"
QUEUE_PATH = ROOT / "data" / "social_pack_queue.json"
JST = ZoneInfo("Asia/Tokyo")

SKIP_WORDS = ("オープニング", "エンディング", "次回予告")


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clip(value: str, limit: int) -> str:
    value = _clean(value)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _episode_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _next_jst(hour: int, minute: int, now: datetime) -> datetime:
    candidate = now.astimezone(JST).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now.astimezone(JST):
        candidate += timedelta(days=1)
    return candidate


def build_items(latest: dict, now: datetime) -> list[dict]:
    source_url = _clean(latest.get("listen_episode_url"))
    title = _clean(latest.get("title"))
    if not source_url.startswith("https://listen.style/p/reelpal/") or not title:
        return []

    chapter_titles = []
    for chapter in latest.get("chapters") or []:
        chapter_title = _clean(chapter.get("title") if isinstance(chapter, dict) else "")
        if not chapter_title or any(word in chapter_title for word in SKIP_WORDS):
            continue
        if chapter_title not in chapter_titles:
            chapter_titles.append(chapter_title)
    if len(chapter_titles) < 3:
        return []

    first, middle, last = chapter_titles[0], chapter_titles[len(chapter_titles) // 2], chapter_titles[-1]
    episode_id = _episode_id(source_url)
    due_one = _next_jst(12, 10, now)
    due_two = due_one + timedelta(days=3)

    first_short = _clip(first, 58)
    middle_short = _clip(middle, 58)
    last_short = _clip(last, 58)
    title_short = _clip(title, 64)

    return [
        {
            "id": f"auto-{episode_id}-three-hooks",
            "kind": "three_hooks",
            "text": (
                "今回のリルパル、話の入口はこの3つ。\n"
                f"①{first_short}\n②{middle_short}\n③{last_short}\n"
                "一つの作品から、今回もだいぶ遠くまで行きました。"
            ),
            "source_url": source_url,
            "not_before": due_one.isoformat(),
            "created_at": now.astimezone(JST).isoformat(),
        },
        {
            "id": f"auto-{episode_id}-episode-hook",
            "kind": "episode_hook",
            "text": (
                f"『{title_short}』回。\n\n"
                f"{first_short}から始まり、{middle_short}を通って、最後は{last_short}まで。"
                "映画一本から話がどこへ転がるか、その道筋ごと楽しめる回です。"
            ),
            "source_url": source_url,
            "not_before": due_two.isoformat(),
            "created_at": now.astimezone(JST).isoformat(),
        },
    ]


def enqueue_latest(now: datetime | None = None) -> int:
    now = now or datetime.now(JST)
    try:
        latest = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    if not isinstance(latest, dict) or not isinstance(queue, list):
        return 0

    source_url = _clean(latest.get("listen_episode_url"))
    # Hand-written packs take precedence.  Never add an automatic paraphrase
    # for an episode that already has editorially selected material.
    if source_url and any(_clean(item.get("source_url")) == source_url for item in queue):
        return 0

    existing_ids = {_clean(item.get("id")) for item in queue if isinstance(item, dict)}
    additions = [item for item in build_items(latest, now) if item["id"] not in existing_ids]
    if not additions:
        return 0

    queue.extend(additions)
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] auto-enqueued {len(additions)} grounded social pack items for {source_url}")
    return len(additions)


if __name__ == "__main__":
    enqueue_latest()

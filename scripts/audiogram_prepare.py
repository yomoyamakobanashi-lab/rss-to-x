#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import feedparser

ROOT = Path(__file__).resolve().parents[1]
QUEUE_PATH = ROOT / "data" / "audio_clip_queue.json"
STATE_PATH = ROOT / "state_audio_clip.json"
FEEDS_PATH = ROOT / "feeds.json"
OUT_PATH = ROOT / "audiogram_meta.json"


def norm(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[#＃【】〖〗『』「」\[\]()（）'\"“”‘’]", "", text)
    text = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠ー]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    aa, bb = norm(a), norm(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.92
    return SequenceMatcher(None, aa, bb).ratio()


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def podcast_feed_url() -> str:
    cfg = load_json(FEEDS_PATH, {})
    for feed in cfg.get("feeds", []):
        if isinstance(feed, dict) and feed.get("type") == "podcast" and feed.get("url"):
            return str(feed["url"])
    raise RuntimeError("podcast feed missing from feeds.json")


def enclosure_url(entry) -> str | None:
    for enc in getattr(entry, "enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        if href:
            return str(href)
    for link in getattr(entry, "links", []) or []:
        if str(link.get("rel") or "").lower() == "enclosure":
            href = link.get("href")
            if href:
                return str(href)
    return None


def main() -> None:
    queue = load_json(QUEUE_PATH, [])
    state = load_json(STATE_PATH, {})
    posted = {str(x) for x in state.get("posted_ids", [])}
    if not isinstance(queue, list):
        raise RuntimeError("audio clip queue must be a list")

    item = next((x for x in queue if str(x.get("id")) not in posted), None)
    if item is None:
        print("[INFO] audio clip queue exhausted")
        return

    start = float(item.get("start_seconds", -1))
    duration = float(item.get("duration_seconds", 0))
    if start < 0 or not (15 <= duration <= 35):
        raise RuntimeError(f"invalid audio clip timing: {item}")
    listen_url = str(item.get("listen_url") or "")
    if not listen_url.startswith("https://listen.style/p/reelpal/"):
        raise RuntimeError("audio clip must point to ReelPal LISTEN")

    parsed = feedparser.parse(podcast_feed_url())
    matches = []
    for entry in parsed.entries:
        audio_url = enclosure_url(entry)
        if not audio_url:
            continue
        score = similarity(str(item.get("episode_title") or ""), str(getattr(entry, "title", "") or ""))
        matches.append((score, entry, audio_url))
    if not matches:
        raise RuntimeError("RSS returned no playable enclosures")

    score, entry, audio_url = max(matches, key=lambda x: x[0])
    if score < 0.48:
        raise RuntimeError(
            f"could not safely match LISTEN episode to RSS; score={score:.3f}; "
            f"wanted={item.get('episode_title')}; best={getattr(entry, 'title', '')}"
        )

    payload = {
        "id": str(item["id"]),
        "episode_title": str(item["episode_title"]),
        "rss_title": str(getattr(entry, "title", "") or ""),
        "listen_url": listen_url,
        "audio_url": audio_url,
        "start_seconds": start,
        "duration_seconds": duration,
        "topic": str(item.get("topic") or ""),
        "caption": str(item.get("caption") or ""),
        "match_score": round(float(score), 3),
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[OK] audiogram candidate id={payload['id']} score={payload['match_score']} "
        f"start={start:.1f}s duration={duration:.1f}s"
    )
    print(f"[OK] RSS title: {payload['rss_title']}")


if __name__ == "__main__":
    main()

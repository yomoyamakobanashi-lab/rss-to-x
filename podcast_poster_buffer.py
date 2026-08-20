#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List

import feedparser

from buffer_client import BufferError, post_text
from podcast_poster import (
    MAX_TWEET_LIMIT,
    TITLE_MAXLEN,
    compose_text,
    entries_newest_first,
    entry_timestamp,
    load_state,
    minutes_since,
    pick_best_link_for_podcast,
    save_state,
    shorten_title,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("podcast_poster_buffer")

# New-episode announcements should be timely, but must never backfill old episodes.
MIN_AGE_MINUTES = int(os.getenv("PODCAST_MIN_AGE_MINUTES", "15"))
MAX_CONTENT_AGE_HOURS = int(os.getenv("PODCAST_MAX_CONTENT_AGE_HOURS", "48"))


def main() -> None:
    try:
        with open("feeds.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        logger.error("feeds.json 読み込み失敗: %s", exc)
        sys.exit(2)

    state = load_state()
    candidates: List[Dict[str, Any]] = []

    for feed in cfg.get("feeds", []):
        if not isinstance(feed, dict) or feed.get("type") != "podcast":
            continue

        url = feed.get("url")
        template = feed.get("template")
        program = feed.get("program_name", "")
        if not url or not template:
            continue

        logger.info("Fetching RSS: %s", url)
        parsed = feedparser.parse(url)
        if getattr(parsed, "bozo", False):
            logger.warning("RSS parse warning: %s", getattr(parsed, "bozo_exception", None))

        entries = entries_newest_first(parsed)
        if not entries:
            logger.info("RSSにエントリなし: %s", url)
            continue

        # Only the single newest entry is eligible. This prevents historical backfill.
        entry = entries[0]
        age_minutes = minutes_since(entry)

        if age_minutes < MIN_AGE_MINUTES:
            logger.info(
                "最新回は公開直後のため待機: %.1f min < %d min",
                age_minutes,
                MIN_AGE_MINUTES,
            )
            continue

        if age_minutes > MAX_CONTENT_AGE_HOURS * 60:
            logger.info(
                "最新回は告知対象期間外: %.1f h > %d h",
                age_minutes / 60.0,
                MAX_CONTENT_AGE_HOURS,
            )
            continue

        uid_src = (
            getattr(entry, "id", None)
            or getattr(entry, "guid", None)
            or getattr(entry, "link", None)
            or getattr(entry, "title", None)
        )
        uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
        if uid in state:
            logger.info("最新回はすでに告知済み")
            continue

        link = pick_best_link_for_podcast(entry, feed)
        if not link:
            # Do not mark it as posted: the next hourly run will retry after
            # Apple/Spotify has had time to expose a public episode URL.
            logger.info("Apple/Spotify URL未解決。次回実行で再試行: %s", getattr(entry, "title", ""))
            continue

        title = shorten_title(getattr(entry, "title", "") or "", maxlen=TITLE_MAXLEN)
        text = compose_text(template, title, program, link, limit=MAX_TWEET_LIMIT)
        candidates.append({
            "ts": entry_timestamp(entry),
            "uid": uid,
            "text": text,
        })

    if not candidates:
        logger.info("新着告知候補なし")
        return

    chosen = sorted(candidates, key=lambda c: -c["ts"])[0]
    logger.info("Posting new episode announcement through Buffer:\n%s", chosen["text"])

    try:
        post_id = post_text(chosen["text"])
    except BufferError as exc:
        logger.error("Buffer投稿失敗: %s", exc)
        sys.exit(4)

    # Persist only after Buffer has accepted the post.
    state[chosen["uid"]] = int(time.time())
    save_state(state)
    logger.info("Buffer accepted new episode announcement: %s", post_id)


if __name__ == "__main__":
    main()

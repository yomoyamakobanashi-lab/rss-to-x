#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import logging
import sys
import time
from typing import Any, Dict, List

import feedparser

from buffer_client import BufferError, post_text
from podcast_poster import (
    CHECK_ITEMS,
    FRESH_WAIT_MIN,
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
        entries = entries_newest_first(parsed)

        for entry in entries[:CHECK_ITEMS]:
            uid_src = (
                getattr(entry, "id", None)
                or getattr(entry, "guid", None)
                or getattr(entry, "link", None)
                or getattr(entry, "title", None)
            )
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state:
                continue
            if minutes_since(entry) < FRESH_WAIT_MIN:
                continue

            link = pick_best_link_for_podcast(entry, feed)
            if not link:
                logger.info("Apple/Spotify URL未解決: %s", getattr(entry, "title", ""))
                continue

            title = shorten_title(getattr(entry, "title", "") or "", maxlen=TITLE_MAXLEN)
            text = compose_text(template, title, program, link, limit=MAX_TWEET_LIMIT)
            candidates.append({
                "ts": entry_timestamp(entry),
                "uid": uid,
                "text": text,
            })

    if not candidates:
        logger.info("投稿候補なし")
        return

    chosen = sorted(candidates, key=lambda c: -c["ts"])[0]
    logger.info("Posting through Buffer:\n%s", chosen["text"])

    try:
        post_id = post_text(chosen["text"])
    except BufferError as exc:
        logger.error("Buffer投稿失敗: %s", exc)
        sys.exit(4)

    state[chosen["uid"]] = int(time.time())
    save_state(state)
    logger.info("Buffer accepted podcast post: %s", post_id)


if __name__ == "__main__":
    main()

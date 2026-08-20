#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import re
import sys
import time

import feedparser
import requests

from buffer_client import BufferError, post_text
from note_poster import (
    CHECK_ITEMS,
    FRESH_WAIT_MIN,
    TITLE_MAXLEN,
    compose_text,
    entries_newest_first,
    load_state,
    minutes_since,
    save_state,
    shorten_title,
)


def fetch_note_og_url(url: str) -> str | None:
    try:
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).text
        for pattern in (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ):
            match = re.search(pattern, html, re.I)
            if match:
                return match.group(1)
    except Exception as exc:
        print(f"[WARN] OG image lookup failed: {exc}")
    return None


def main() -> None:
    try:
        cfg = json.load(open("feeds.json", encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] feeds.json: {exc}")
        sys.exit(2)

    state = load_state()

    for feed in cfg.get("feeds", []):
        if feed.get("type") != "note":
            continue

        url = feed.get("url")
        template = feed.get("template")
        program = feed.get("program_name", "")
        if not url or not template:
            continue

        parsed = feedparser.parse(url)
        for entry in entries_newest_first(parsed)[:CHECK_ITEMS]:
            uid_src = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title")
            uid = hashlib.sha256((url + "|" + str(uid_src)).encode("utf-8")).hexdigest()
            if uid in state or minutes_since(entry) < FRESH_WAIT_MIN:
                continue

            link = (entry.get("link") or "").strip()
            if not link:
                continue

            title = shorten_title(entry.get("title") or "", maxlen=TITLE_MAXLEN)
            text = compose_text(template, title, program, link, limit=280)
            image_url = fetch_note_og_url(link)

            try:
                post_id = post_text(text, image_url=image_url)
            except BufferError as exc:
                print(f"[ERROR] Buffer note post failed: {exc}")
                sys.exit(4)

            state[uid] = int(time.time())
            save_state(state)
            print(f"[OK] Buffer accepted note post: {post_id} image={'yes' if image_url else 'no'}")
            return

    print("[INFO] no eligible note candidates")


if __name__ == "__main__":
    main()
